"""
Severity and the issue ledger.

Sep/Oct-2026: the integrity gate failed the build on ANY issue, so Bangladesh
Bank being late with two publications turned every refresh red — about 28 runs
a day for things nobody could act on. Meanwhile the daily digest, the one alarm
meant to be read, had died on its first run. A real failure would have been
indistinguishable from that wallpaper.

The fix is a distinction these tests pin down: stale data because BB has not
published (waiting — visible, aged, never red) versus stale data because OUR
fetch is broken (defect — always red). They are opposite problems that look
identical in the data, and the evidence that separates them is whether our own
fetch step ran without error.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate
from db import InterbankRepo, PipelineRun, PrimaryYieldSnapshot, DataIssue
from engines.guardian import record, record_repairs, open_issues, fingerprint

D = datetime.date
NOW = datetime.datetime.utcnow


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _stale_repo(s, days=6):
    """A repo row stale enough to trip the freshness rule but still INSIDE the
    dataset's tolerance, so it is BB being late rather than us being broken."""
    s.add(InterbankRepo(trade_date=D.today() - datetime.timedelta(days=days),
                        num_deals=30, amount_crore=2979.24, tenor_min_days=1,
                        tenor_max_days=7, rate_min_pct=8.5, rate_max_pct=8.95,
                        war_pct=8.8, ingested_utc=NOW()))


def _run(s, *, hours_ago=1.0, errors=None, guardian_ran=True):
    """A refresh run. Production always stores a quality payload carrying the
    guardian's ledger result; `guardian_ran=False` models the case where the
    guardian did not run at all, which the heartbeat has to catch."""
    quality = {"ok": True, "issue_count": 0}
    if guardian_ran:
        quality["ledger"] = {"opened": 0, "resolved": 0, "open_now": 0}
    s.add(PipelineRun(run_utc=NOW() - datetime.timedelta(hours=hours_ago), kind="refresh",
                      errors=json.dumps(errors) if errors else None, elapsed_sec=10,
                      quality=json.dumps(quality)))


def _check(monkeypatch, eng):
    monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
    return validate.integrity_check()


def _repo_lines(rep, key):
    return [x for x in rep[key] if x.startswith("freshness: repo")]


class TestSeverity:
    def test_bb_late_is_waiting_and_does_not_fail_the_build(self, monkeypatch):
        # Our fetch ran an hour ago and reported no error for this step, and the
        # gap is still within the dataset's tolerance, so this is BB being late.
        # It must be visible but must NOT turn the build red.
        eng, s = _mem(); _stale_repo(s); _run(s); s.commit()
        rep = _check(monkeypatch, eng)
        assert _repo_lines(rep, "waiting"), rep["waiting"]
        assert not _repo_lines(rep, "issues")
        assert rep["waiting_count"] >= 1

    def test_our_broken_fetch_is_a_defect_and_does_fail_the_build(self, monkeypatch):
        # Same stale data, but our own step errored — now it is ours to fix.
        eng, s = _mem(); _stale_repo(s)
        _run(s, errors=["interbank_repo: HTTPError 503"]); s.commit()
        rep = _check(monkeypatch, eng)
        assert _repo_lines(rep, "issues"), rep["issues"]
        assert not _repo_lines(rep, "waiting")
        assert rep["ok"] is False

    def test_the_same_staleness_reclassifies_when_the_fetch_breaks(self, monkeypatch):
        # The heart of the change: identical data, opposite verdicts, decided
        # only by whether OUR fetch is working.
        eng, s = _mem(); _stale_repo(s); _run(s); s.commit()
        assert _repo_lines(_check(monkeypatch, eng), "waiting")

        s.query(PipelineRun).delete()
        _run(s, errors=["interbank_repo: connection refused"]); s.commit()
        after = _check(monkeypatch, eng)
        assert _repo_lines(after, "issues") and not _repo_lines(after, "waiting")

    def test_a_dead_pipeline_makes_everything_our_defect(self, monkeypatch):
        # If the refresh has not run at all, nothing can be blamed on BB —
        # we have no evidence we even looked.
        eng, s = _mem(); _stale_repo(s); _run(s, hours_ago=100); s.commit()
        rep = _check(monkeypatch, eng)
        assert _repo_lines(rep, "issues") and not _repo_lines(rep, "waiting")

    def test_waiting_expires_into_a_defect(self, monkeypatch):
        """The failure this whole split got wrong.

        For eleven days OMO data was missing while the fetch reported success,
        so the staleness stayed `waiting` -- which never fails a build and never
        alerts. validate's own message even called the gap implausible. Beyond
        the dataset's tolerance the verdict must flip, however healthy the fetch
        claims to be: 'BB is a day late' and 'BB has stopped publishing' look
        identical in the data, and only elapsed time separates them.
        """
        eng, s = _mem()
        _stale_repo(s, days=12)          # far beyond a daily series' tolerance
        _run(s)                          # and OUR fetch says it is fine
        s.commit()
        rep = _check(monkeypatch, eng)
        esc = [i for i in rep["issues"] if i.startswith("freshness: repo")]
        assert esc, f"11 days stale must escalate, got waiting={rep['waiting']}"
        assert "ESCALATED" in esc[0]
        assert not _repo_lines(rep, "waiting"), "it must leave waiting entirely"

    def test_the_escalation_says_whose_fault_it_is(self, monkeypatch):
        # A fetch that reports success while storing nothing is succeeding at
        # fetching nothing; the message has to say so or it reads as BB's fault.
        eng, s = _mem(); _stale_repo(s, days=12); _run(s); s.commit()
        msg = [i for i in _check(monkeypatch, eng)["issues"]
               if i.startswith("freshness: repo")][0]
        assert "ours, not BB" in msg
        assert "succeeding at" in msg

    def test_a_wrong_value_is_always_a_defect(self, monkeypatch):
        # Severity applies to FRESHNESS only. A bad number is ours no matter how
        # healthy the fetch is — BB's lateness can never excuse a wrong value.
        eng, s = _mem(); _run(s)
        s.add(PrimaryYieldSnapshot(snapshot_date=D.today(), auction_date=D.today(),
                                   issue_date=D.today() + datetime.timedelta(days=1),
                                   security_type="T_BOND", tenor_label="10Y", tenor_years=10,
                                   cutoff_yield_pct=99.0))
        s.commit()
        rep = _check(monkeypatch, eng)
        assert any("implausible cut-off" in i for i in rep["issues"]), rep["issues"]
        assert rep["ok"] is False


class TestTheGuardianWatchesItself:
    """An alarm that can go quiet without anyone hearing is not an alarm, and it
    is the one thing no data check reveals: a silent guardian looks exactly like
    a clean system."""

    def _guardian(self, rep):
        return [i for i in rep["issues"] if i.startswith("guardian:")]

    def test_a_refresh_that_did_not_run_the_guardian_is_a_defect(self, monkeypatch):
        eng, s = _mem()
        _run(s, guardian_ran=False)
        s.commit()
        found = self._guardian(_check(monkeypatch, eng))
        assert found, "a run with no ledger result must be reported"
        assert "not running inside the pipeline" in found[0]

    def test_a_healthy_run_is_silent(self, monkeypatch):
        eng, s = _mem()
        _run(s)
        s.commit()
        assert self._guardian(_check(monkeypatch, eng)) == []

    def test_a_stopped_pipeline_is_itself_the_finding(self, monkeypatch):
        # Every freshness check below would be measuring data nobody is trying
        # to update, so the staleness is a symptom and this is the cause.
        eng, s = _mem()
        _run(s, hours_ago=96)
        s.commit()
        found = self._guardian(_check(monkeypatch, eng))
        assert found and "no pipeline run" in found[0]


class TestLedger:
    def _report(self, waiting=(), issues=()):
        return {"issues": list(issues), "waiting": list(waiting),
                "by_table": {}, "by_table_waiting": {}}

    def test_the_same_problem_on_successive_days_is_one_row(self):
        # Dates change daily while the problem does not; digits are normalised
        # out of the fingerprint so the age keeps growing instead of resetting.
        eng, s = _mem()
        record(s, self._report(waiting=["freshness: repo: nothing newer than 2026-09-24"]))
        record(s, self._report(waiting=["freshness: repo: nothing newer than 2026-09-25"]))
        s.commit()
        rows = s.query(DataIssue).all()
        assert len(rows) == 1, [r.message for r in rows]
        assert rows[0].occurrences == 2 and rows[0].resolved_at is None

    def test_resolution_records_how_long_it_lived(self):
        eng, s = _mem()
        old = NOW() - datetime.timedelta(days=3)
        record(s, self._report(issues=["omo: 2026-09-01 something wrong"]), now=old)
        s.commit()
        record(s, self._report())                       # gone on the next run
        s.commit()
        row = s.query(DataIssue).one()
        assert row.resolved_at is not None
        assert "3d" in row.resolution, row.resolution

    def test_a_returning_problem_keeps_its_original_first_seen(self):
        # A recurring fault should read as recurring, not as brand new each time.
        eng, s = _mem()
        first = NOW() - datetime.timedelta(days=5)
        record(s, self._report(issues=["omo: 2026-09-01 flapping"]), now=first)
        record(s, self._report())
        r = record(s, self._report(issues=["omo: 2026-09-09 flapping"]))
        s.commit()
        row = s.query(DataIssue).one()
        assert r["reopened"] == 1
        assert row.resolved_at is None
        assert abs((row.first_seen - first).total_seconds()) < 2

    def test_severity_can_flip_on_an_existing_issue(self):
        # BB-late becoming our bug must update the row, not open a second one.
        eng, s = _mem()
        record(s, self._report(waiting=["freshness: repo: nothing newer than 2026-09-24"]))
        record(s, self._report(issues=["freshness: repo: nothing newer than 2026-09-24"]))
        s.commit()
        rows = s.query(DataIssue).all()
        assert len(rows) == 1 and rows[0].severity == "defect"

    def test_open_issues_puts_defects_first_then_oldest(self):
        eng, s = _mem()
        record(s, self._report(waiting=["freshness: repo: waiting"],
                               issues=["omo: real problem"]))
        s.commit()
        out = open_issues(s)
        assert [o["severity"] for o in out] == ["defect", "waiting"]

    def test_repairs_are_recorded_for_audit(self):
        eng, s = _mem()
        n = record_repairs(s, {"changes": ["yield_issue_date: 91D 2026-09-06 -> 2026-09-07",
                                           "coupon_payment_date: BD0001 moved"]})
        s.commit()
        assert n == 2
        from db import DataChange
        assert s.query(DataChange).count() == 2

    def test_fingerprint_ignores_digits_only(self):
        assert fingerprint("repo stale since 2026-09-24") == fingerprint("repo stale since 2026-10-01")
        assert fingerprint("repo stale") != fingerprint("fx stale")


class TestSentinelHonoursSeverity:
    """The sentinel is a separate alarm channel and must not re-introduce the
    noise the severity split removed — otherwise it simply becomes the new place
    BB's lateness turns things red."""

    def _payload(self, waiting):
        ts = datetime.datetime.utcnow().isoformat()
        return {
            "last_run": ts, "last_run_errors": [],
            "data_health": {"ok": True, "issue_count": 0, "by_table": {}},
            "waiting": waiting,
            "datasets": {"repo": {"label": "Interbank Repo", "kind": "daily", "current": False}},
        }

    def _run(self, monkeypatch, payload):
        import json as _json
        import sentinel
        monkeypatch.setattr(sentinel, "_http", lambda url, timeout=25: (
            (200, _json.dumps(payload)) if "/api/meta/status" in url
            else (200, _json.dumps({"commitSha": "deadbee"})) if "deploy-status" in url
            else (200, "ok")))
        monkeypatch.setattr(sentinel.subprocess, "check_output", lambda *a, **k: b"deadbee")
        return sentinel.check()

    def test_a_series_behind_because_bb_is_late_is_not_an_alert(self, monkeypatch):
        v = self._run(monkeypatch, self._payload(
            [{"message": "freshness: repo: BB has published nothing newer", "age_days": 6}]))
        assert v["ok"] is True, v["problems"]

    def test_the_same_series_behind_for_any_other_reason_still_alerts(self, monkeypatch):
        v = self._run(monkeypatch, self._payload([]))
        assert v["ok"] is False
        assert any("Interbank Repo" in p for p in v["problems"]), v["problems"]


class TestTheDigestLeadsWithEscalations:
    """The eleven-day OMO outage was IN the digest the whole time.

    It was one bullet, below an all-clear summary line, inside a list truncated
    at ten items, in the same grey tone as "BB is a day late". Being present in
    a report nobody can triage is not a notification. An escalation — our fetch
    is broken, or a feed has been dead past its tolerance — now goes above the
    summary, in bold, uncapped. If this ordering regresses, the alarm goes back
    to being wallpaper, which is the exact failure the desk found by hand.
    """

    def _body(self, monkeypatch, issues, close_db=True):
        import digest
        eng, s = _mem()
        s.add(PipelineRun(run_utc=NOW(), kind="refresh", errors="[]",
                          quality=json.dumps({"ledger": {"opened": 0}})))
        s.commit()
        monkeypatch.setattr(digest, "get_session", lambda: s)
        monkeypatch.setattr(digest, "integrity_check", lambda: {
            "ok": not issues, "issue_count": len(issues),
            "by_table": {"freshness": len(issues)}, "issues": issues,
            "waiting": [],
        })
        body, problems = digest.build()
        return body

    def test_an_escalation_appears_above_the_summary_line(self, monkeypatch):
        esc = "freshness: omo: ESCALATED — nothing newer than 2026-09-29, 7 working days"
        body = self._body(monkeypatch, ["freshness: secondary: 1 day missing", esc])
        assert "ESCALATION(S)" in body
        assert body.index(esc) < body.index("**Our data:"), \
            "the escalation must be read before the defect count, not after it"

    def test_escalations_are_never_cut_by_the_ten_item_cap(self, monkeypatch):
        # Twelve ordinary findings would have pushed the escalation out of the
        # list entirely. That is how a dead feed stays invisible.
        issues = [f"freshness: s{i}: 1 day missing" for i in range(12)]
        esc = "freshness: omo: ESCALATED — nothing newer than 2026-09-29"
        body = self._body(monkeypatch, issues + [esc])
        assert esc in body, "an escalation was dropped by the truncation cap"
        assert "and 2 more" in body, "truncation must say how many it hid"

    def test_a_clean_run_says_so_without_inventing_an_escalation_heading(self, monkeypatch):
        body = self._body(monkeypatch, [])
        assert "all checks pass" in body
        assert "ESCALATION" not in body
