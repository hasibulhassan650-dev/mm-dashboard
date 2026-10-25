"""The guardian watches the watch.

A news watch that stops produces no events, and a page with no events looks exactly
like a calm week. So the heartbeat is judged on whether the watch READ anything - not on
what it found - and a backlog waiting for Claude is a defect, not a quiet queue. The
sukuk line of the ladder is held to BB's outstanding table the same way the buyback line
is held to its events.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate

D = datetime.date
NOW = datetime.datetime.utcnow()


def _check(monkeypatch, populate):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    populate(s)
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    rep = validate.integrity_check(limit_per_rule=500)
    return rep


def _mine(rep, prefix, bucket="issues"):
    return [i for i in rep.get(bucket, []) if i.startswith(prefix)]


def _article(s, hours_ago, status="extracted", url="u"):
    s.add(dbmod.NewsArticle(url=f"{url}{hours_ago}{status}{len(s.new)}", source="x", title="t",
                            fetched_utc=NOW - datetime.timedelta(hours=hours_ago), triage_status=status))


class TestNewsHeartbeat:
    def test_a_watch_that_stopped_is_a_defect(self, monkeypatch):
        rep = _check(monkeypatch, lambda s: _article(s, 9))
        assert any("has not read an article for" in i for i in _mine(rep, "news-watch"))

    def test_a_running_watch_is_clean(self, monkeypatch):
        rep = _check(monkeypatch, lambda s: _article(s, 1))
        assert _mine(rep, "news-watch") == []

    def test_a_watch_that_never_ran_is_a_notice_not_a_red_build(self, monkeypatch):
        """Day one: the workflow has simply not fired yet. Loud, but not a failed build."""
        rep = _check(monkeypatch, lambda s: None)
        assert _mine(rep, "news-watch") == []
        assert any("never run" in i for i in _mine(rep, "news-watch", "notices"))

    def test_a_backlog_waiting_for_claude_is_a_defect(self, monkeypatch):
        def pop(s):
            _article(s, 0.5, "extracted")
            for i in range(25):
                s.add(dbmod.NewsArticle(url=f"w{i}", source="x", title="t", triage_status="untriaged",
                                        fetched_utc=NOW - datetime.timedelta(hours=3)))
        rep = _check(monkeypatch, pop)
        assert any("waited over 2h for Claude" in i for i in _mine(rep, "news-watch"))

    def test_a_small_recent_backlog_is_not(self, monkeypatch):
        def pop(s):
            _article(s, 0.5, "extracted")
            for i in range(5):
                s.add(dbmod.NewsArticle(url=f"w{i}", source="x", title="t", triage_status="untriaged",
                                        fetched_utc=NOW - datetime.timedelta(hours=3)))
        assert _mine(_check(monkeypatch, pop), "news-watch") == []

    def test_a_news_finding_does_not_swallow_the_other_findings(self, monkeypatch):
        """Regression: a local variable named `waiting` once shadowed the list the whole
        severity machinery appends to, and every later finding crashed."""
        rep = _check(monkeypatch, lambda s: [_article(s, 9)] + [s.add(dbmod.NewsArticle(
            url=f"w{i}", source="x", title="t", triage_status="untriaged",
            fetched_utc=NOW - datetime.timedelta(hours=3))) for i in range(25)])
        assert isinstance(rep["issues"], list) and rep["issue_count"] >= 2


class TestSukukLadderLine:
    def _pop(self, flow_sukuk):
        def pop(s):
            s.add(dbmod.SukukOutstanding(isin="BDS1", project="p", issue_date=D(2025, 12, 29),
                                         maturity_date=D(2026, 12, 29), outstanding_crore=1000.0,
                                         profit_rate_pct=None, profit_frequency="on Maturity"))
            s.add(dbmod.DailyNetFlow(flow_date=D(2026, 12, 29), sukuk_inflow_bdt_mill=flow_sukuk,
                                     coupon_inflow_bdt_mill=0, principal_inflow_bdt_mill=0,
                                     total_inflow_bdt_mill=flow_sukuk, auction_outflow_confirmed_mill=0,
                                     auction_outflow_best_mill=0, data_complete=True))
        return pop

    def test_a_ladder_that_matches_the_table_is_clean(self, monkeypatch):
        rep = _check(monkeypatch, self._pop(10000.0))              # 1,000 cr = 10,000 mill
        assert [i for i in _mine(rep, "ladder-recon") if "sukuk" in i] == []

    def test_a_stale_ladder_is_a_defect_naming_the_day(self, monkeypatch):
        rep = _check(monkeypatch, self._pop(0.0001))
        hits = [i for i in _mine(rep, "ladder-recon") if "sukuk" in i]
        assert hits and "2026-12-29" in hits[0]

    def test_a_ladder_with_a_sukuk_day_the_table_no_longer_has_is_a_defect(self, monkeypatch):
        def pop(s):
            s.add(dbmod.SukukOutstanding(isin="BDS1", project="p", issue_date=D(2025, 12, 29),
                                         maturity_date=D(2027, 6, 29), outstanding_crore=1000.0,
                                         profit_frequency="on Maturity"))
            s.add(dbmod.DailyNetFlow(flow_date=D(2026, 12, 29), sukuk_inflow_bdt_mill=10000.0,
                                     coupon_inflow_bdt_mill=0, principal_inflow_bdt_mill=0,
                                     total_inflow_bdt_mill=10000.0, auction_outflow_confirmed_mill=0,
                                     auction_outflow_best_mill=0, data_complete=True))
            s.add(dbmod.DailyNetFlow(flow_date=D(2027, 6, 29), coupon_inflow_bdt_mill=0,
                                     principal_inflow_bdt_mill=0, total_inflow_bdt_mill=0,
                                     auction_outflow_confirmed_mill=0, auction_outflow_best_mill=0,
                                     data_complete=True))
        rep = _check(monkeypatch, pop)
        assert any("sukuk" in i for i in _mine(rep, "ladder-recon"))


def test_the_news_workflow_exists_with_its_secret_and_no_cancel_on_progress():
    wf = open(os.path.join(_ROOT, ".github", "workflows", "news_watch.yml"), encoding="utf-8").read()
    assert "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}" in wf
    assert "cancel-in-progress: false" in wf and "engines.news_triage" in wf and "engines.announce publish" in wf
    assert "workflow_dispatch" in wf and "*/10" not in wf


def test_the_requirements_pin_anthropic_with_an_upper_bound():
    req = open(os.path.join(_ROOT, "requirements.txt"), encoding="utf-8").read()
    assert "anthropic>=" in req and "<3" in req
