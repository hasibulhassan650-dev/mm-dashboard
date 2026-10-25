"""A news event reaches the desk once, only if it matters and BB's data does not hold it.

The announcer already emails a buyback or a sukuk notice; a liquidity event read from the
news is the same channel with two extra conditions (matters, not redundant) and a body that
says plainly it is not BB data and not in any ladder.
"""
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, NewsArticle
from engines import announce as A

NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _ev(s, **kw):
    base = dict(dedupe_key=f"k{len(s.query(LiquidityEvent).all())}", kind="SUBSIDY_RELEASE", direction="INJECT",
                amount_crore=4000.0, status="DISBURSED", channel="CASH", counterparty="Petrobangla",
                summary="LNG subsidy released", evidence_quote="The Finance Division has released Tk 4,000 crore",
                sources_json=json.dumps([{"url": "https://x/1", "source": "tbs_economy", "title": "t"}]),
                confidence=0.9, matters=True, first_seen_utc=NOW - datetime.timedelta(hours=1),
                updated_utc=NOW, event_date=datetime.date(2026, 10, 19))
    base.update(kw)
    e = LiquidityEvent(**base)
    s.add(e)
    s.commit()
    return e


class _Run:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)

        class R:
            returncode, stdout, stderr = 0, "[]", ""
        return R()

    def created(self):
        return [c for c in self.calls if c[1:3] == ["issue", "create"]]


class TestWhatIsAnnounced:
    def test_a_matters_event_opens_one_issue_with_its_key_in_the_title(self):
        s = _mem()
        e = _ev(s)
        r = _Run()
        out = A.publish(s, runner=r, now=NOW)
        assert out["opened"] == [f"ev-{e.id}"]
        title = r.created()[0][r.created()[0].index("--title") + 1]
        assert title.startswith("Liquidity news:") and f"[ev-{e.id}]" in title

    def test_the_body_says_it_is_not_bb_data_and_not_in_a_total(self):
        s = _mem()
        _ev(s)
        r = _Run()
        A.publish(s, runner=r, now=NOW)
        body = r.created()[0][r.created()[0].index("--body") + 1]
        assert "4,000 crore" in body and "The Finance Division has released Tk 4,000 crore" in body
        assert "not included in any ladder" in body and "https://x/1" in body

    def test_below_the_threshold_is_not_announced(self):
        s = _mem()
        _ev(s, matters=False)
        assert A.pending(s, now=NOW) == []

    def test_an_event_BBs_own_data_already_holds_is_not_announced(self):
        s = _mem()
        _ev(s, redundant_with="omo_transactions")
        assert A.pending(s, now=NOW) == []

    def test_it_is_announced_once(self):
        s = _mem()
        _ev(s)
        A.publish(s, runner=_Run(), now=NOW)
        r2 = _Run()
        assert A.publish(s, runner=r2, now=NOW)["pending"] == 0 and r2.calls == []

    def test_a_failed_issue_leaves_it_pending(self):
        s = _mem()
        _ev(s)

        class Bad(_Run):
            def __call__(self, cmd, **kw):
                r = super().__call__(cmd, **kw)
                if cmd[1:3] == ["issue", "create"]:
                    r.returncode, r.stderr = 1, "HTTP 502"
                return r
        out = A.publish(s, runner=Bad(), now=NOW)
        assert out["failed"] and len(A.pending(s, now=NOW)) == 1

    def test_the_press_and_sukuk_paths_are_untouched(self):
        s = _mem()
        _ev(s)
        s.add(dbmod.PressRelease(serial="pr1", published_date=NOW.date(), title="Buyback", url="u",
                                 category="BUYBACK", first_seen_utc=NOW, last_seen_utc=NOW))
        s.commit()
        assert {p["serial"] for p in A.pending(s, now=NOW)} == {"pr1", "ev-1"}


class TestTheDigest:
    def test_a_new_event_is_listed(self):
        s = _mem()
        s.add(NewsArticle(url="u", source="x", title="t", fetched_utc=NOW, triage_status="extracted"))
        _ev(s)
        lines, problems = A.news_digest_lines(s, now=NOW)
        txt = "\n".join(lines)
        assert "Liquidity news not in BB's data" in txt and "4,000 cr" in txt and "Petrobangla" in txt

    def test_a_watch_that_has_not_run_is_a_problem(self):
        s = _mem()
        s.add(NewsArticle(url="u", source="x", title="t", fetched_utc=NOW - datetime.timedelta(hours=9),
                          triage_status="extracted"))
        s.commit()
        assert any("has not run" in p for p in A.news_digest_lines(s, now=NOW)[1])

    def test_a_watch_that_never_ran_is_a_problem(self):
        assert any("has not run" in p for p in A.news_digest_lines(_mem(), now=NOW)[1])

    def test_a_pile_of_articles_waiting_for_claude_is_a_problem(self):
        """Missing key or failing calls must be loud, not a quiet backlog."""
        s = _mem()
        for i in range(25):
            s.add(NewsArticle(url=f"u{i}", source="x", title="t", triage_status="untriaged",
                              fetched_utc=NOW - datetime.timedelta(hours=3)))
        s.add(NewsArticle(url="fresh", source="x", title="t", triage_status="extracted", fetched_utc=NOW))
        s.commit()
        assert any("waiting for Claude" in p for p in A.news_digest_lines(s, now=NOW)[1])

    def test_a_healthy_quiet_watch_has_no_problem(self):
        s = _mem()
        s.add(NewsArticle(url="u", source="x", title="t", fetched_utc=NOW, triage_status="skipped"))
        s.commit()
        assert A.news_digest_lines(s, now=NOW)[1] == []
