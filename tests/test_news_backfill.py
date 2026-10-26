"""The watch catches up on history by itself.

Nobody should have to search the web and paste links in. The backfill asks Claude to search,
month by month, only Bangladesh's own outlets - and keeps ONLY the URLs, which then go through
the same reading and the same guards as a live article. What these tests protect: the model's
prose is never trusted, history is never announced as news, and a search that fails is reported
instead of quietly yielding an empty history.
"""
import datetime
import json
import os
import sys
from types import SimpleNamespace as NS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, NewsArticle
from engines import announce as A
from engines import news_backfill as B
from engines import news_triage as T

NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)
LNG = ("The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla for the "
       "month of September, officials said. The money will be used to pay import bills.")


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _raw():
    return {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 4000.0,
            "event_date": "2026-09-20", "announced_date": "2026-09-21", "status": "DISBURSED",
            "channel": "CASH", "counterparty": "Petrobangla", "summary": "LNG subsidy released",
            "evidence_quote": "The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla",
            "confidence": 0.9}


class FakeClaude:
    def __init__(self):
        self.calls, self.messages = [], self

    def create(self, **kw):
        self.calls.append(kw)
        txt = (json.dumps({"is_candidate": True}) if kw["model"] == T.SCREEN_MODEL
               else json.dumps({"events": [_raw()]}))
        return NS(stop_reason="end_turn", content=[NS(type="text", text=txt)])


def _found(url="https://www.tbsnews.net/economy/lng-subsidy-1", title="Govt gives Tk 4,000cr for gas imports",
           age="September 21, 2026"):
    return {"url": url, "title": title, "published_utc": B._page_age(age)}


class TestMonths:
    def test_covers_every_month_from_the_start_date(self):
        assert B.months_between(datetime.date(2026, 7, 1), datetime.date(2026, 10, 25)) == [
            "July 2026", "August 2026", "September 2026", "October 2026"]

    def test_crosses_a_year_end(self):
        assert B.months_between(datetime.date(2026, 11, 3), datetime.date(2027, 1, 5)) == [
            "November 2026", "December 2026", "January 2027"]


class TestReadingSearchResults:
    def _resp(self, content, stop="end_turn"):
        return NS(stop_reason=stop, content=content)

    def test_urls_titles_and_dates_come_from_the_result_blocks_only(self):
        blk = NS(type="web_search_tool_result", content=[
            NS(type="web_search_result", url="https://a/1", title="T1", page_age="October 20, 2026"),
            NS(type="web_search_result", url="https://a/2", title="T2", page_age=None)])
        prose = NS(type="text", text="I found that Tk 99,999 crore was released")
        out = B.results_of(self._resp([prose, blk]))
        assert [r["url"] for r in out] == ["https://a/1", "https://a/2"]
        assert out[0]["published_utc"] == datetime.datetime(2026, 10, 20) and out[1]["published_utc"] is None

    def test_the_models_prose_is_never_an_input(self):
        """A search summary is an unverifiable claim; only fetched, quoted text is believed."""
        prose = NS(type="text", text="Tk 99,999 crore released to BPDB")
        assert B.results_of(self._resp([prose])) == []

    def test_an_error_result_is_skipped_not_raised(self):
        err = NS(type="web_search_tool_result", content=NS(type="web_search_tool_result_error", error_code="max_uses_exceeded"))
        assert B.results_of(self._resp([err])) == []

    def test_an_unreadable_date_is_none_not_a_guess(self):
        assert B._page_age("3 weeks ago") is None
        assert B._page_age(None) is None and B._page_age("") is None

    def test_the_search_request_is_restricted_to_bangladeshi_outlets_and_forces_nothing(self):
        c = FakeClaude()
        c.create = lambda **kw: (c.calls.append(kw), NS(stop_reason="end_turn", content=[]))[1]
        B.discover(c, "LNG subsidy July 2026")
        kw = c.calls[0]
        tool = kw["tools"][0]
        assert tool["type"] == "web_search_20260209" and tool["allowed_domains"] == B.ALLOWED_DOMAINS
        assert "tool_choice" not in kw and "temperature" not in kw
        assert "thedailystar.net" in B.ALLOWED_DOMAINS and "tbsnews.net" in B.ALLOWED_DOMAINS

    def test_a_refusal_is_an_error(self):
        c = FakeClaude()
        c.create = lambda **kw: NS(stop_reason="refusal", content=[])
        try:
            B.discover(c, "q")
            raise AssertionError("expected a TriageError")
        except T.TriageError:
            pass


class TestTheBackfill:
    def _run(self, s, discover_fn, **kw):
        return B.backfill(s, FakeClaude(), datetime.date(2026, 7, 1), now=NOW,
                          queries=["lng {month}"], fetch_body=lambda u: LNG, discover_fn=discover_fn, **kw)

    def test_a_found_article_becomes_a_stored_event_by_the_normal_route(self):
        s = _mem()
        st = self._run(s, lambda c, q: [_found()] if "September" in q else [])
        assert st["new_articles"] == 1 and st["triage"]["extracted"] == 1
        e = s.query(LiquidityEvent).one()
        assert e.amount_crore == 4000.0 and e.counterparty == "Petrobangla"
        assert s.query(NewsArticle).one().source == "search:web"

    def test_history_is_marked_announced_so_it_is_never_emailed_as_news(self):
        s = _mem()
        self._run(s, lambda c, q: [_found()] if "September" in q else [])
        assert s.query(LiquidityEvent).one().notified_utc is not None
        assert A.pending(s, now=NOW) == []

    def test_an_article_found_by_search_skips_the_title_gate_but_not_the_guards(self):
        """The title says nothing about an actor; the search already judged it relevant. It is
        still only believed if a verbatim quote is in the page."""
        s = _mem()
        calls = []
        B.backfill(s, FakeClaude(), datetime.date(2026, 7, 1), now=NOW, queries=["q {month}"],
                   fetch_body=lambda u: calls.append(u) or LNG,
                   discover_fn=lambda c, q: [_found(title="Gas payments")] if "September" in q else [])
        assert calls and s.query(LiquidityEvent).count() == 1

    def test_a_page_that_does_not_contain_the_quote_yields_no_event(self):
        s = _mem()
        B.backfill(s, FakeClaude(), datetime.date(2026, 7, 1), now=NOW, queries=["q {month}"],
                   fetch_body=lambda u: "The Finance Division has released Tk 4,000 crore for something else entirely, said officials to the press on Monday.",
                   discover_fn=lambda c, q: [_found()] if "September" in q else [])
        assert s.query(LiquidityEvent).count() == 0

    def test_the_same_url_found_twice_is_stored_once(self):
        s = _mem()
        st = self._run(s, lambda c, q: [_found()])
        assert st["new_articles"] == 1

    def test_a_search_that_fails_is_reported_and_the_rest_continue(self):
        s = _mem()

        def disc(c, q):
            if "July" in q:
                raise RuntimeError("503")
            return [_found()] if "September" in q else []
        st = self._run(s, disc)
        assert len(st["search_errors"]) == 1 and "July" in st["search_errors"][0]
        assert st["new_articles"] == 1

    def test_the_search_budget_stops_it_and_says_so(self):
        s = _mem()
        st = self._run(s, lambda c, q: [], max_searches=2)
        assert st["searches"] == 2 and st["budget_hit"]

    def test_items_before_the_start_date_are_not_stored(self):
        s = _mem()
        st = self._run(s, lambda c, q: [_found(age="March 3, 2026")])
        assert st["new_articles"] == 0


class TestTheWorkflowAndCli:
    def _wf(self):
        return open(os.path.join(_ROOT, ".github", "workflows", "news_watch.yml"), encoding="utf-8").read()

    def test_the_workflow_can_be_told_to_backfill(self):
        wf = self._wf()
        assert "backfill" in wf and "since" in wf and '--since "${{ inputs.since' in wf

    def test_a_backfill_never_announces(self):
        assert "inputs.action != 'backfill'" in self._wf()

    def test_a_backfill_without_a_key_says_so_and_fails(self, monkeypatch, capsys):
        s = _mem()
        monkeypatch.setattr(T, "get_client", lambda: None)
        import db
        monkeypatch.setattr(db, "get_session", lambda: s)
        monkeypatch.setattr(db, "init_db", lambda: None)
        assert T.main(["backfill"]) == 1
        assert "ANTHROPIC_API_KEY" in capsys.readouterr().out

    def test_the_queries_cover_the_kinds_the_user_asked_about(self):
        txt = " ".join(B.QUERIES).lower()
        for must in ("lng", "subsidy", "liquidity support", "crr", "refinance", "special bond", "savings certificate"):
            assert must in txt, must
