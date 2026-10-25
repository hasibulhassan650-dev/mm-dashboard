"""The desk is told when BB announces something that matters.

BB announced Bangladesh's first bond buyback on 05-Oct-2026 and its result on
the 8th, and nothing here reacted to either: 1,716.14 crore reached the market
on the 12th with no line in the ladder. The listing is stored now; this is the
part that makes the answer arrive on its own.

The channel is a GitHub issue per release (the repo already emails its owner),
so the properties that matter are about not flooding it and not losing things:
what is announced, what is not, when a release counts as done, and that
switching it on never replays history.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import PressRelease
from engines import announce as A

NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)
D = datetime.date


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _rel(s, serial, category, age_days=0.5, title=None, notified=False):
    s.add(PressRelease(
        serial=serial, published_date=(NOW - datetime.timedelta(days=age_days)).date(),
        subject_date=None, title=title or f"{category} release {serial}",
        url=f"https://example.invalid/{serial}.pdf", category=category,
        first_seen_utc=NOW - datetime.timedelta(days=age_days),
        last_seen_utc=NOW, notified_utc=NOW if notified else None))
    s.commit()


class _Run:
    """A stand-in for subprocess.run that records every gh call."""

    def __init__(self, existing=(), fail_create=(), fail_list=False):
        self.calls, self.existing, self.fail_create = [], set(existing), set(fail_create)
        self.fail_list = fail_list

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)

        class R:
            returncode, stdout, stderr = 0, "[]", ""

        r = R()
        if cmd[1:3] == ["issue", "list"]:
            r.stdout = json.dumps([{"title": f"BB announced: x [{sr}]"} for sr in self.existing])
            if self.fail_list:
                r.returncode, r.stderr = 1, "HTTP 500"
        if cmd[1:3] == ["issue", "create"]:
            title = cmd[cmd.index("--title") + 1]
            if any(sr in title for sr in self.fail_create):
                r.returncode, r.stderr = 1, "HTTP 502"
        return r

    def created(self):
        return [c[c.index("--title") + 1] for c in self.calls if c[1:3] == ["issue", "create"]]


class TestWhatIsWorthAnEmail:
    def test_the_high_signal_categories_are_announced(self):
        s = _mem()
        for i, c in enumerate(("BUYBACK", "AUCTION_NOTICE", "BGIIB", "SUKUK", "POLICY")):
            _rel(s, f"pr{i}", c)
        assert {p["category"] for p in A.pending(s, now=NOW)} == {
            "BUYBACK", "AUCTION_NOTICE", "BGIIB", "SUKUK", "POLICY"}

    def test_routine_omo_and_results_are_not(self):
        """Sixty OMO releases would fill the inbox, and an inbox that fills
        teaches you to stop reading - the failure this whole system avoids."""
        s = _mem()
        for i, c in enumerate(("OMO", "TBILL_RESULT", "TBOND_RESULT", "FX", "OTHER")):
            _rel(s, f"pr{i}", c)
        assert A.pending(s, now=NOW) == []

    def test_an_already_announced_release_is_not_announced_again(self):
        s = _mem()
        _rel(s, "pr1", "BUYBACK", notified=True)
        assert A.pending(s, now=NOW) == []


class TestSwitchingItOnNeverReplaysHistory:
    def test_old_releases_are_not_announced(self):
        s = _mem()
        _rel(s, "pr1", "BUYBACK", age_days=10)
        assert A.pending(s, now=NOW) == []

    def test_baseline_marks_everything_stored_as_already_told(self):
        """Without it the first run would announce whatever was first seen in the
        last few days - releases the desk has long since read."""
        s = _mem()
        _rel(s, "pr1", "BUYBACK", age_days=1)
        _rel(s, "pr2", "BGIIB", age_days=0.2)
        assert len(A.pending(s, now=NOW)) == 2
        assert A.baseline(s, now=NOW) == 2
        s.commit()
        assert A.pending(s, now=NOW) == []

    def test_first_run_is_detected_only_when_nothing_was_ever_announced(self):
        """main() baselines on first_run, so history never floods the inbox - but a
        table where something HAS been announced must never be re-baselined, or a
        genuinely new buyback would be silently swallowed."""
        s = _mem()
        assert not A.first_run(s), "an empty table is not a first run"
        _rel(s, "pr1", "BUYBACK")
        assert A.first_run(s)
        _rel(s, "pr2", "BUYBACK", notified=True)
        assert not A.first_run(s)

    def test_a_cap_stops_a_bug_from_sending_fifty_emails(self):
        s = _mem()
        for i in range(30):
            _rel(s, f"pr{i:03d}", "BGIIB", age_days=0.5)
        out = A.pending(s, now=NOW)
        assert len(out) == A.CAP_PER_RUN
        # the most recent ones, not the oldest
        assert out[-1]["serial"] == "pr029"


class TestAReleaseIsDoneOnlyOnceTheIssueExists:
    def test_one_issue_per_release_titled_with_bbs_serial(self):
        s = _mem()
        _rel(s, "pr14322", "BUYBACK", title="Press Release regarding the Buyback of 2 Year Treasury bonds")
        run = _Run()
        out = A.publish(s, runner=run, now=NOW)
        assert out["opened"] == ["pr14322"]
        assert len(run.created()) == 1 and "[pr14322]" in run.created()[0]
        assert A.pending(s, now=NOW) == [], "an opened issue must mark the release done"

    def test_a_failed_issue_is_not_marked_so_it_is_retried(self):
        """A notification that failed and was marked done is a notification the
        desk never gets - the silent-loss failure."""
        s = _mem()
        _rel(s, "pr1", "BUYBACK")
        out = A.publish(s, runner=_Run(fail_create={"pr1"}), now=NOW)
        assert out["failed"] and not out["opened"]
        assert [p["serial"] for p in A.pending(s, now=NOW)] == ["pr1"]

    def test_one_failure_does_not_block_the_others(self):
        s = _mem()
        _rel(s, "pr1", "BUYBACK")
        _rel(s, "pr2", "BGIIB")
        out = A.publish(s, runner=_Run(fail_create={"pr1"}), now=NOW)
        assert out["opened"] == ["pr2"] and len(out["failed"]) == 1

    def test_an_existing_issue_is_not_duplicated_but_is_marked_done(self):
        """A rerun after a crash between 'issue created' and 'row marked' must
        not email twice - the serial in the title is how it finds its own."""
        s = _mem()
        _rel(s, "pr1", "BUYBACK")
        run = _Run(existing={"pr1"})
        out = A.publish(s, runner=run, now=NOW)
        assert out["already_open"] == ["pr1"] and run.created() == []
        assert A.pending(s, now=NOW) == []

    def test_a_failed_listing_sends_nothing_blind(self):
        """Without the dedupe listing a rerun could email twice; no listing, no send."""
        s = _mem()
        _rel(s, "pr1", "BUYBACK")
        run = _Run(fail_list=True)
        out = A.publish(s, runner=run, now=NOW)
        assert out["failed"] and run.created() == []
        assert [p["serial"] for p in A.pending(s, now=NOW)] == ["pr1"]

    def test_nothing_pending_makes_no_gh_calls_at_all(self):
        s = _mem()
        run = _Run()
        out = A.publish(s, runner=run, now=NOW)
        assert out["pending"] == 0 and run.calls == []

    def test_the_body_links_to_bb_and_says_why_it_matters(self):
        body = A.issue_body({"category": "BUYBACK", "published_date": D(2026, 10, 8),
                             "subject_date": None, "url": "https://x/pr1.pdf", "serial": "pr1"})
        assert "https://x/pr1.pdf" in body and "pr1" in body
        assert "shrinks the bond" in body or "settlement" in body


class TestTheDigest:
    def test_lists_high_signal_and_collapses_routine(self):
        s = _mem()
        _rel(s, "pr1", "BUYBACK", age_days=0.2, notified=True)
        _rel(s, "pr2", "OMO", age_days=0.2)
        _rel(s, "pr3", "OMO", age_days=0.2)
        lines, problems = A.digest_lines(s, now=NOW)
        text = "\n".join(lines)
        assert "pr1" in text and "2 routine release(s)" in text
        assert "pr2" not in text and problems == []

    def test_a_release_that_was_never_announced_is_a_problem(self):
        """If the announcer silently stopped, this is the only place it shows."""
        s = _mem()
        _rel(s, "pr9", "BUYBACK", age_days=1)          # 24h old, never sent
        lines, problems = A.digest_lines(s, now=NOW)
        assert problems and "pr9" in "\n".join(lines)

    def test_a_fresh_unsent_release_is_not_yet_a_problem(self):
        s = _mem()
        _rel(s, "pr9", "BUYBACK", age_days=0.01)       # minutes old: next hourly run
        assert A.digest_lines(s, now=NOW)[1] == []


def test_the_hourly_workflow_runs_it():
    """An announcer nothing calls is a feature that was never delivered."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wf = open(os.path.join(root, ".github", "workflows", "hourly_refresh.yml"),
              encoding="utf-8").read()
    assert "engines/announce.py publish" in wf or "-m engines.announce publish" in wf
    assert "GH_TOKEN" in wf
