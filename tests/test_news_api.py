"""What the Liquidity Watch page and day page are told.

The rule that matters: a news event is shown BESIDE BB's figures, never inside a total.
An event with no stated amount is counted as an event and never as zero, and an event
BB's own tables already hold is hidden unless asked for.
"""
import ast
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, NewsArticle
import news_logic as NL

D = datetime.date
TODAY = D(2026, 10, 25)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


_n = [0]


def _ev(s, **kw):
    _n[0] += 1
    base = dict(dedupe_key=f"k{_n[0]}", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=4000.0,
                status="DISBURSED", counterparty="Petrobangla", summary="s", evidence_quote="q",
                sources_json=json.dumps([{"url": "https://x/1", "source": "tbs"}]), confidence=0.9,
                matters=True, event_date=D(2026, 10, 19), first_seen_utc=datetime.datetime(2026, 10, 20))
    base.update(kw)
    s.add(LiquidityEvent(**base))
    s.commit()


class TestTotals:
    def test_a_month_and_the_fiscal_year_are_summed_apart(self):
        s = _mem()
        _ev(s)                                           # Oct, 4000
        _ev(s, event_date=D(2026, 8, 5), amount_crore=1000.0)    # Aug, in FY27
        _ev(s, event_date=D(2026, 5, 5), amount_crore=999.0)     # May: before 1-Jul, not FY27
        t = NL.news_block(s, today=TODAY)["totals"]
        assert t["month_inject_crore"] == 4000.0 and t["fy_inject_crore"] == 5000.0

    def test_fiscal_year_starts_on_the_first_of_july(self):
        assert NL.fiscal_year_start(D(2026, 10, 25)) == D(2026, 7, 1)
        assert NL.fiscal_year_start(D(2027, 3, 1)) == D(2026, 7, 1)
        assert NL.fiscal_year_start(D(2026, 6, 30)) == D(2025, 7, 1)

    def test_injections_and_absorptions_are_kept_apart(self):
        s = _mem()
        _ev(s)
        _ev(s, kind="BB_BILL", direction="ABSORB", amount_crore=700.0)
        t = NL.news_block(s, today=TODAY)["totals"]
        assert t["month_inject_crore"] == 4000.0 and t["month_absorb_crore"] == 700.0

    def test_an_event_with_no_amount_is_counted_as_an_event_never_as_zero(self):
        s = _mem()
        _ev(s)
        _ev(s, kind="RESERVE_REQ_CUT", amount_crore=None)
        t = NL.news_block(s, today=TODAY)["totals"]
        assert t["events_counted"] == 2 and t["events_without_amount"] == 1
        assert t["month_inject_crore"] == 4000.0

    def test_a_neutral_event_is_listed_but_never_summed(self):
        s = _mem()
        _ev(s, kind="BUDGET_SUPPORT_FX", direction="NEUTRAL", amount_crore=9999.0)
        t = NL.news_block(s, today=TODAY)["totals"]
        assert t["month_inject_crore"] == 0 and t["month_absorb_crore"] == 0

    def test_a_redundant_event_is_not_double_counted(self):
        s = _mem()
        _ev(s)
        _ev(s, redundant_with="omo_transactions", amount_crore=3000.0)
        assert NL.news_block(s, today=TODAY)["totals"]["month_inject_crore"] == 4000.0

    def test_a_minor_event_is_not_counted(self):
        s = _mem()
        _ev(s, matters=False, amount_crore=300.0)
        assert NL.news_block(s, today=TODAY)["totals"]["month_inject_crore"] == 0


class TestWhatIsShown:
    def test_redundant_and_minor_are_hidden_by_default_and_counted(self):
        s = _mem()
        _ev(s)
        _ev(s, redundant_with="buyback_events")
        _ev(s, matters=False)
        b = NL.news_block(s, today=TODAY)
        assert b["count"] == 1 and b["hidden"] == {"redundant": 1, "minor": 1}

    def test_they_can_be_asked_for(self):
        s = _mem()
        _ev(s)
        _ev(s, redundant_with="buyback_events")
        _ev(s, matters=False)
        assert NL.news_block(s, today=TODAY, show_redundant=True, show_minor=True)["count"] == 3

    def test_sources_are_parsed_and_dates_are_strings(self):
        s = _mem()
        _ev(s)
        e = NL.news_block(s, today=TODAY)["events"][0]
        assert e["sources"][0]["url"] == "https://x/1" and e["event_date"] == "2026-10-19" and e["when"] == "2026-10-19"

    def test_events_outside_the_window_are_dropped(self):
        s = _mem()
        _ev(s, event_date=D(2025, 1, 1))
        assert NL.news_block(s, today=TODAY, days=120)["count"] == 0

    def test_an_event_with_no_date_falls_back_to_when_it_was_first_seen(self):
        s = _mem()
        _ev(s, event_date=None, announced_date=None)
        assert NL.news_block(s, today=TODAY)["events"][0]["when"] == "2026-10-20"

    def test_the_page_is_told_the_watch_has_a_backlog(self):
        s = _mem()
        for i in range(3):
            s.add(NewsArticle(url=f"u{i}", source="x", title="t", fetched_utc=datetime.datetime(2026, 10, 25),
                              triage_status="untriaged"))
        s.commit()
        h = NL.news_block(s, today=TODAY)["health"]
        assert h["articles_waiting_for_claude"] == 3 and h["articles_by_status"] == {"untriaged": 3}

    def test_it_says_in_words_that_this_is_not_bb_data(self):
        assert "NOT Bangladesh Bank data" in NL.news_block(_mem(), today=TODAY)["note"]


class TestTheDayPage:
    def test_only_that_days_matters_and_non_redundant_events_are_listed(self):
        s = _mem()
        _ev(s)
        _ev(s, event_date=D(2026, 10, 20))
        _ev(s, redundant_with="omo_transactions")
        _ev(s, matters=False)
        assert len(NL.events_on(s, "2026-10-19")) == 1

    def test_the_day_page_never_adds_them_to_a_total(self):
        src = open(os.path.join(_ROOT, "api", "routers", "flows.py"), encoding="utf-8").read()
        assert '"news_events": news_events' in src
        for f in ("liquidity_logic.py", "model_logic.py"):
            body = open(os.path.join(_ROOT, "api", "routers", f), encoding="utf-8").read()
            assert "liquidity_events" not in body, f
        agg = open(os.path.join(_ROOT, "engines", "aggregation.py"), encoding="utf-8").read()
        assert "liquidity_events" not in agg


def test_it_respects_the_api_deploy_boundary():
    for f in ("news_logic.py", "news.py"):
        src = open(os.path.join(_ROOT, "api", "routers", f), encoding="utf-8").read()
        mods = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) and n.level == 0
                else (n.module or "") for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
        assert not mods & {"engines", "fetchers", "config", "calendar_utils"}, (f, mods)
