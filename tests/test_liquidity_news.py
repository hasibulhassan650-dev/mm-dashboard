"""Liquidity Watch: events in the news that BB's own data does not hold.

The risk in reading news with a language model is a confident event that is not in
the article. Every guard below exists because the opposite failure is stored as
fact: a quote that is not in the text, an amount the quote does not support, a
direction that contradicts the kind. The model is faked throughout - no network.
"""
import datetime
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, LiquidityEventHistory, NewsArticle
from engines import liquidity_news as L
from engines import news_triage as T
from fetchers import news as N

D = datetime.date
NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


LNG = ("The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla for the "
       "month of September, officials said. The money will be used to pay import bills.")
ART = {"url": "https://x.example/lng", "source": "tbs_economy", "title": "Govt releases Tk 4,000 crore LNG subsidy",
       "summary": "", "body_text": LNG, "published_utc": datetime.datetime(2026, 10, 20, 8, 0)}


def _raw(**kw):
    base = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 4000.0,
            "event_date": "2026-10-19", "announced_date": "2026-10-20", "status": "DISBURSED",
            "channel": "CASH", "counterparty": "Petrobangla", "summary": "LNG subsidy released",
            "evidence_quote": "The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla",
            "confidence": 0.9}
    base.update(kw)
    return base


class TestThePrefilter:
    def test_a_subsidy_release_passes(self):
        assert L.keyword_score("Govt releases Tk 2,500cr subsidy to Petrobangla for LNG import")[1]

    def test_cricket_does_not(self):
        assert not L.keyword_score("Bangladesh beat Sri Lanka by 5 wickets")[1]

    def test_an_actor_alone_is_not_enough(self):
        assert not L.keyword_score("Bangladesh Bank governor visits Bhutan", "courtesy call")[1]

    def test_a_crr_change_needs_no_amount(self):
        assert L.keyword_score("Bangladesh Bank cuts CRR by 50 basis points")[1]

    def test_short_acronyms_match_whole_words_only(self):
        assert not L._has("bpc", "bpcl exports")
        assert L._has("bpc", "the bpc said")


class TestAmountsAreReadByCode:
    @pytest.mark.parametrize("text,expect", [
        ("Tk 4,000 crore", [4000.0]), ("Tk4,000cr", [4000.0]), ("Tk 47 billion", [4700.0]),
        ("Tk 1.2 lakh crore", [120000.0]), ("Tk 500 million", [50.0]), ("4,100 crore taka", [4100.0]),
        ("BDT 2,500 crore", [2500.0]), ("Tk 4,000", []), ("$1.2 billion", []), ("", [])])
    def test_units(self, text, expect):
        assert L.amounts_crore(text) == expect

    def test_the_quote_decides_not_the_model(self):
        assert L.verify_amount(4700, "Tk 47 billion was disbursed") == (4700.0, False)

    def test_a_model_amount_the_quote_does_not_support_is_dropped_and_flagged(self):
        assert L.verify_amount(4000, "Tk 47 billion was disbursed") == (None, True)

    def test_a_missing_amount_is_taken_from_the_quote_when_it_states_exactly_one(self):
        assert L.verify_amount(None, "released Tk 4,000 crore") == (4000.0, False)
        assert L.verify_amount(None, "Tk 4,000 crore of Tk 9,000 crore") == (None, False)

    def test_one_percent_tolerance(self):
        assert L.verify_amount(4030, "Tk 4,000 crore")[0] == 4000.0
        assert L.verify_amount(4100, "Tk 4,000 crore") == (None, True)


class TestGuardsOnWhatTheModelReports:
    def test_a_good_event_passes(self):
        ev, why = L.validate_event(_raw(), ART)
        assert ev and ev["amount_crore"] == 4000.0 and ev["direction"] == "INJECT" and why == []

    def test_a_quote_that_is_not_in_the_article_is_rejected(self):
        """The whole point: an invented sentence must never become a stored fact."""
        ev, why = L.validate_event(_raw(evidence_quote="The Finance Division released Tk 9,000 crore to BPDB"), ART)
        assert ev is None and "not in the article" in why[0]

    def test_a_quote_differing_only_in_whitespace_and_case_is_accepted(self):
        ev, _ = L.validate_event(_raw(evidence_quote="the finance division   HAS released Tk 4,000 crore as LNG subsidy"), ART)
        assert ev is not None

    def test_a_too_short_quote_is_rejected(self):
        assert L.validate_event(_raw(evidence_quote="Tk 4,000 crore"), ART)[0] is None

    def test_an_unknown_kind_is_rejected(self):
        assert L.validate_event(_raw(kind="MYSTERY"), ART)[0] is None

    def test_a_direction_that_contradicts_the_kind_is_rejected(self):
        assert L.validate_event(_raw(direction="ABSORB"), ART)[0] is None

    def test_an_amount_the_quote_does_not_support_is_nulled_not_stored(self):
        ev, why = L.validate_event(_raw(amount_crore=9999.0), ART)
        assert ev["amount_crore"] is None and ev["amount_unverified"] and why

    def test_an_implausible_date_is_dropped(self):
        ev, _ = L.validate_event(_raw(event_date="2019-01-01"), ART)
        assert ev["event_date"] is None

    def test_a_bad_status_or_channel_becomes_none(self):
        ev, _ = L.validate_event(_raw(status="WHATEVER", channel="X"), ART)
        assert ev["status"] is None and ev["channel"] is None

    def test_confidence_is_clamped(self):
        assert L.validate_event(_raw(confidence=7), ART)[0]["confidence"] == 1.0


class TestWhatMatters:
    def _e(self, kind="SUBSIDY_RELEASE", amt=4000.0, conf=0.9):
        return {"kind": kind, "amount_crore": amt, "confidence": conf}

    def test_500_crore_or_more_matters(self):
        assert L.matters(self._e(amt=500.0)) and not L.matters(self._e(amt=499.0))

    def test_300_crore_of_refinance_is_hidden(self):
        assert not L.matters(self._e(kind="BB_REFINANCE", amt=300.0))

    def test_a_crr_cut_matters_whatever_its_size(self):
        assert L.matters(self._e(kind="RESERVE_REQ_CUT", amt=None))

    def test_bb_bank_support_matters_without_an_amount(self):
        assert L.matters(self._e(kind="BB_BANK_SUPPORT", amt=None))

    def test_low_confidence_never_matters(self):
        assert not L.matters(self._e(conf=0.5)) and not L.matters(self._e(kind="RESERVE_REQ_CUT", conf=0.4))

    def test_an_unknown_amount_on_an_ordinary_kind_does_not_matter(self):
        assert not L.matters(self._e(amt=None))


class TestClustering:
    def _store(self, s, raw, art):
        ev, _ = L.validate_event(raw, art)
        return L.merge_or_create(s, ev, art, NOW)

    def test_two_articles_about_one_release_make_one_event_with_two_sources(self):
        s = _mem()
        a1, how1 = self._store(s, _raw(), ART)
        art2 = {**ART, "url": "https://y.example/lng2", "source": "dailystar_economy"}
        a2, how2 = self._store(s, _raw(), art2)
        assert (how1, how2) == ("created", "merged") and a1.id == a2.id
        assert s.query(LiquidityEvent).count() == 1
        assert len(json.loads(a2.sources_json)) == 2

    def test_the_same_article_twice_adds_no_source(self):
        s = _mem()
        self._store(s, _raw(), ART)
        row, _ = self._store(s, _raw(), ART)
        assert len(json.loads(row.sources_json)) == 1

    def test_a_status_advance_is_recorded_not_overwritten(self):
        s = _mem()
        self._store(s, _raw(status="ANNOUNCED"), ART)
        row, how = self._store(s, _raw(status="DISBURSED"), {**ART, "url": "https://z.example/3"})
        assert how == "merged" and row.status == "DISBURSED"
        h = s.query(LiquidityEventHistory).filter_by(field="status").one()
        assert (h.old_value, h.new_value) == ("ANNOUNCED", "DISBURSED")

    def test_a_status_never_goes_backwards(self):
        s = _mem()
        self._store(s, _raw(status="DISBURSED"), ART)
        row, _ = self._store(s, _raw(status="ANNOUNCED"), {**ART, "url": "https://z.example/3"})
        assert row.status == "DISBURSED"

    def test_a_different_amount_is_a_different_event(self):
        s = _mem()
        self._store(s, _raw(), ART)
        art2 = {**ART, "url": "https://q.example/9", "body_text": LNG.replace("4,000", "2,500")}
        raw2 = _raw(amount_crore=2500.0, evidence_quote="The Finance Division has released Tk 2,500 crore as LNG subsidy to Petrobangla")
        self._store(s, raw2, art2)
        assert s.query(LiquidityEvent).count() == 2

    def test_a_late_amount_fills_a_null_one(self):
        s = _mem()
        art0 = {**ART, "body_text": LNG.replace("Tk 4,000 crore ", "an undisclosed sum ")}
        raw0 = _raw(amount_crore=None, evidence_quote="The Finance Division has released an undisclosed sum as LNG subsidy to Petrobangla")
        self._store(s, raw0, art0)
        row, how = self._store(s, _raw(), {**ART, "url": "https://z.example/4"})
        assert how == "merged" and row.amount_crore == 4000.0


class TestRedundancy:
    """The user's rule: do not tell me what BB's own data already says."""

    def _ev(self, **kw):
        base = {"kind": "BB_BANK_SUPPORT", "amount_crore": 1000.0, "event_date": D(2026, 10, 19),
                "announced_date": D(2026, 10, 19), "summary": "", "evidence_quote": ""}
        base.update(kw)
        return base

    def test_an_sls_event_matching_an_omo_row_is_redundant(self):
        s = _mem()
        s.add(dbmod.OMOTransaction(transaction_date=D(2026, 10, 19), maturity_date=D(2026, 10, 26),
                                   instrument="SLS", direction="INJECTION", accepted_bdt_crore=1000.0,
                                   tenor_label="7D", tenor_days=7))
        s.commit()
        art = {"title": "BB gives Tk 1,000 crore special liquidity support (SLS) to a bank", "url": "https://x/1"}
        assert L.redundancy(s, self._ev(), art) == "omo_transactions"

    def test_an_sls_event_of_a_different_size_is_not(self):
        s = _mem()
        s.add(dbmod.OMOTransaction(transaction_date=D(2026, 10, 19), maturity_date=D(2026, 10, 26),
                                   instrument="SLS", direction="INJECTION", accepted_bdt_crore=100.0,
                                   tenor_label="7D", tenor_days=7))
        s.commit()
        art = {"title": "BB gives Tk 1,000 crore special liquidity support (SLS) to a bank", "url": "https://x/1"}
        assert L.redundancy(s, self._ev(), art) is None

    def test_a_buyback_story_is_redundant_once_the_event_is_stored(self):
        s = _mem()
        s.add(dbmod.BuybackEvent(isin="BD0926191026", settlement_date=D(2026, 10, 12), accepted_face_crore=1716.14))
        s.commit()
        ev = self._ev(kind="GOVT_REPAY_BB", event_date=D(2026, 10, 12), announced_date=D(2026, 10, 12))
        assert L.redundancy(s, ev, {"title": "Govt buyback of bonds settles", "url": "https://x/2"}) == "buyback_events"

    def test_an_fx_purchase_matching_an_fx_auction_is_redundant(self):
        s = _mem()
        s.add(dbmod.FxAuctionResult(auction_date=D(2026, 10, 18), auction_type="BUY"))
        s.commit()
        ev = self._ev(kind="BB_FX_BUY", event_date=D(2026, 10, 19))
        assert L.redundancy(s, ev, {"title": "BB buys $50m", "url": "https://x/3"}) == "fx_auction_results"

    def test_an_lng_subsidy_is_never_redundant(self):
        s = _mem()
        ev = self._ev(kind="SUBSIDY_RELEASE", amount_crore=4000.0)
        assert L.redundancy(s, ev, ART) is None

    def test_a_bb_press_release_url_is_redundant(self):
        s = _mem()
        assert L.redundancy(s, self._ev(), {"title": "x", "url": "https://www.bb.org.bd/mediaroom/press_release/pr1.pdf"}) \
            == "press_releases"


class _Resp:
    def __init__(self, obj, stop="end_turn"):
        self.stop_reason = stop
        self.content = [NS(type="thinking", thinking=""), NS(type="text", text=json.dumps(obj))]


class FakeClaude:
    """screen -> is_candidate, extract -> events. Records every call."""

    def __init__(self, candidate=True, events=None, stop=None, boom=None):
        self.calls, self.candidate, self.events, self.stop, self.boom = [], candidate, events, stop, boom
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        if kw["model"] == T.SCREEN_MODEL:
            return _Resp({"is_candidate": self.candidate}, self.stop or "end_turn")
        return _Resp({"events": self.events if self.events is not None else [_raw()]}, self.stop or "end_turn")


def _article(s, url="https://x.example/lng", title=ART["title"], body=LNG, summary="", pub=None):
    a = NewsArticle(url=url, source="tbs_economy", title=title, summary=summary, body_text=body,
                    published_utc=pub or ART["published_utc"], fetched_utc=NOW, triage_status="untriaged")
    s.add(a)
    s.commit()
    return a


class TestTriage:
    def test_end_to_end_a_real_looking_article_becomes_a_stored_matters_event(self):
        s = _mem()
        _article(s)
        c = FakeClaude()
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["extracted"] == 1 and st["events_created"] == 1
        e = s.query(LiquidityEvent).one()
        assert e.amount_crore == 4000.0 and e.matters is True and e.redundant_with is None
        assert [k["model"] for k in c.calls] == [T.SCREEN_MODEL, T.EXTRACT_MODEL]

    def test_a_hallucinated_event_is_rejected_and_counted(self):
        s = _mem()
        _article(s)
        c = FakeClaude(events=[_raw(evidence_quote="The IMF disbursed Tk 9,999 crore to the government today")])
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["events_rejected"] == 1 and s.query(LiquidityEvent).count() == 0

    def test_a_screened_out_article_never_reaches_the_extractor(self):
        s = _mem()
        _article(s)
        c = FakeClaude(candidate=False)
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["screened_out"] == 1 and [k["model"] for k in c.calls] == [T.SCREEN_MODEL]

    def test_an_irrelevant_article_costs_no_model_call_at_all(self):
        s = _mem()
        _article(s, title="Bangladesh beat Sri Lanka", body="Cricket match report from Dhaka.")
        c = FakeClaude()
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["skipped"] == 1 and c.calls == []

    def test_no_key_still_reads_the_article_with_the_free_rule_reader_and_says_so(self):
        """A key is a bill, so no key must not mean no reading (see test_news_rules.py)."""
        s = _mem()
        _article(s)
        st = T.triage_pending(s, None, NOW, fetch_body=lambda u: None)
        assert st["no_key"] and st["mode"] == "rules" and st["untriaged_left"] == 0
        assert s.query(NewsArticle).one().triage_status == "extracted"
        assert any("rule reader" in r for r in st["reasons"])

    def test_a_refusal_is_an_error_to_retry_not_a_silent_skip(self):
        s = _mem()
        _article(s)
        st = T.triage_pending(s, FakeClaude(stop="refusal"), NOW, fetch_body=lambda u: None)
        a = s.query(NewsArticle).one()
        assert st["errors"] == 1 and a.triage_status == "error"

    def test_truncation_is_an_error(self):
        s = _mem()
        _article(s)
        st = T.triage_pending(s, FakeClaude(stop="max_tokens"), NOW, fetch_body=lambda u: None)
        assert st["errors"] == 1

    def test_an_api_exception_is_an_error_and_the_run_continues(self):
        s = _mem()
        _article(s)
        _article(s, url="https://x.example/2")
        st = T.triage_pending(s, FakeClaude(boom=RuntimeError("503")), NOW, fetch_body=lambda u: None)
        assert st["errors"] == 2

    def test_an_errored_article_is_retried_next_run(self):
        s = _mem()
        _article(s)
        T.triage_pending(s, FakeClaude(stop="refusal"), NOW, fetch_body=lambda u: None)
        st = T.triage_pending(s, FakeClaude(), NOW, fetch_body=lambda u: None)
        assert st["extracted"] == 1

    def test_the_per_run_extraction_budget_stops_and_reports(self, monkeypatch):
        monkeypatch.setattr(T, "MAX_EXTRACTS", 2)
        s = _mem()
        for i in range(5):
            _article(s, url=f"https://x.example/{i}")
        st = T.triage_pending(s, FakeClaude(), NOW, fetch_body=lambda u: None)
        assert st["extracted"] == 2 and st["budget_hit"] and st["untriaged_left"] == 3

    def test_the_daily_ceiling_stops_everything(self, monkeypatch):
        monkeypatch.setattr(T, "MAX_CALLS_PER_DAY", 0)
        s = _mem()
        _article(s)
        c = FakeClaude()
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["budget_hit"] and c.calls == []

    def test_a_body_is_fetched_only_for_plausible_candidates(self):
        s = _mem()
        _article(s, url="https://x.example/a", body=None, title="Govt releases Tk 4,000 crore subsidy to Petrobangla")
        _article(s, url="https://x.example/b", body=None, title="Cricket final tonight")
        fetched = []
        T.triage_pending(s, FakeClaude(), NOW, fetch_body=lambda u: fetched.append(u) or LNG)
        assert fetched == ["https://x.example/a"]

    def test_a_headline_that_names_no_actor_is_still_read_when_the_blurb_shows_the_signal(self):
        """'National Bank starts repaying its depositors' named no actor; Bangladesh Bank, the Tk figure
        and the support are in the body. Judging the headline alone dropped a deposit payout unread."""
        s = _mem()
        _article(s, url="https://x.example/nb", body=None, title="National Bank starts repaying its depositors",
                 summary="Liquidity support has reached the bank; disbursement of Tk 500 crore began.")
        fetched = []
        T.triage_pending(s, FakeClaude(), NOW, fetch_body=lambda u: fetched.append(u) or LNG)
        assert fetched == ["https://x.example/nb"]

    def test_running_out_of_body_budget_leaves_it_untriaged_not_skipped(self, monkeypatch):
        monkeypatch.setattr(T, "MAX_BODY_FETCHES", 0)
        s = _mem()
        _article(s, body=None, title="Govt releases Tk 4,000 crore subsidy to Petrobangla")
        T.triage_pending(s, FakeClaude(), NOW, fetch_body=lambda u: LNG)
        assert s.query(NewsArticle).one().triage_status == "untriaged"

    def test_the_request_uses_the_documented_surface(self):
        """Sonnet 5.5 returns 400 on forced tool use, and on a sampling parameter: the
        request must carry neither, and must ask for structured JSON."""
        s = _mem()
        _article(s)
        c = FakeClaude()
        T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        for kw in c.calls:
            assert "tool_choice" not in kw and "temperature" not in kw and "top_p" not in kw
            assert kw["output_config"]["format"]["type"] == "json_schema"
            assert kw["output_config"]["effort"] in ("low", "medium")


class TestFeeds:
    RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Govt releases Tk 4,000cr subsidy</title><link>https://x.example/a/?utm=1#frag</link>
      <pubDate>Mon, 20 Oct 2026 08:00:00 +0600</pubDate><description>&lt;p&gt;Petrobangla gets cash&lt;/p&gt;</description></item>
    <item><title>No link here</title></item></channel></rss>"""

    def test_rss_is_parsed_and_the_url_canonicalised(self):
        it = N.parse_rss(self.RSS)
        assert len(it) == 1 and it[0]["url"] == "https://x.example/a"
        assert it[0]["summary"] == "Petrobangla gets cash"
        assert it[0]["published_utc"] == datetime.datetime(2026, 10, 20, 2, 0)   # +0600 -> UTC

    def test_canonical_url_drops_query_fragment_and_trailing_slash(self):
        assert N.canonical_url("https://A.example/x/?a=1#b") == "https://a.example/x"

    def test_new_items_are_stored_once(self):
        s = _mem()
        items = N.parse_rss(self.RSS)
        assert T.ingest_items(s, "tbs_economy", items, NOW) == 1
        assert T.ingest_items(s, "tbs_economy", items, NOW) == 0
        assert s.query(NewsArticle).count() == 1

    def test_items_before_the_since_date_are_not_stored(self):
        s = _mem()
        assert T.ingest_items(s, "x", N.parse_rss(self.RSS), NOW, since=datetime.datetime(2026, 11, 1)) == 0

    def test_a_feed_that_stops_parsing_is_a_reported_problem(self):
        s = _mem()
        res = T.run(s, None, NOW, sources=[{"key": "dead", "label": "d", "url": "u"}],
                    fetch_listing=lambda src: {"items": [], "error": "HTTP 404", "meta": None},
                    fetch_body=lambda u: None)
        assert res["problems"] == ["dead: HTTP 404"]

    def test_the_registry_has_no_stale_feed(self):
        keys = {s["key"] for s in N.NEWS_SOURCES}
        assert "tbs_all" not in keys           # its newest item was from 2021
        assert len(keys) == len(N.NEWS_SOURCES) >= 8


def test_the_threshold_and_kinds_are_defined_in_code_not_by_the_model():
    assert set(L.KINDS.values()) == {"INJECT", "ABSORB", "NEUTRAL"}
    assert L.MATTERS_MIN_CRORE == 500.0 and L.MATTERS_MIN_CONFIDENCE == 0.6
    assert set(T._KIND_HELP) == set(L.KINDS), "every kind needs a definition in the prompt"


class TestExitCode:
    def test_one_blocked_source_does_not_turn_the_job_red(self, monkeypatch, capsys):
        """The Observer returns 403 to a GitHub runner. A job that is red every half hour
        for one source is a job nobody looks at when it is red for a real reason."""
        s = _mem()
        monkeypatch.setattr(T, "get_client", lambda: None)
        res = {"sources": {"a": 5, "b": 0, "c": 5, "d": 5}, "problems": ["b: HTTP 403"], "new_articles": 0,
               "triage": {"no_key": True, "untriaged_left": 0, "budget_hit": False, "errors": 0}}
        monkeypatch.setattr(T, "run", lambda *a, **k: res)
        import db
        monkeypatch.setattr(db, "get_session", lambda: s)
        monkeypatch.setattr(db, "init_db", lambda: None)
        assert T.main(["run"]) == 0

    def test_most_sources_gone_is_red(self, monkeypatch):
        s = _mem()
        monkeypatch.setattr(T, "get_client", lambda: None)
        res = {"sources": {"a": 0, "b": 0, "c": 5}, "problems": ["a: HTTP 403", "b: HTTP 403"], "new_articles": 0,
               "triage": {"no_key": True, "untriaged_left": 0, "budget_hit": False, "errors": 0}}
        monkeypatch.setattr(T, "run", lambda *a, **k: res)
        import db
        monkeypatch.setattr(db, "get_session", lambda: s)
        monkeypatch.setattr(db, "init_db", lambda: None)
        assert T.main(["run"]) == 1

    def test_a_model_error_is_red(self, monkeypatch):
        s = _mem()
        monkeypatch.setattr(T, "get_client", lambda: None)
        res = {"sources": {"a": 5}, "problems": [], "new_articles": 0,
               "triage": {"no_key": False, "untriaged_left": 0, "budget_hit": False, "errors": 2}}
        monkeypatch.setattr(T, "run", lambda *a, **k: res)
        import db
        monkeypatch.setattr(db, "get_session", lambda: s)
        monkeypatch.setattr(db, "init_db", lambda: None)
        assert T.main(["run"]) == 1


class TestARequestIsNotAnEventWhoeverReportsIt:
    """From the first real Claude call (26-Oct): over a live article it reported
    'Petrobangla has requested Tk 27,000 crore' as a subsidy RELEASE. The quote was verbatim and the
    amount matched, so quote and amount checks passed - only the meaning was wrong. The meaning test
    now applies to every reader's output."""

    ARTICLE = {"title": "LNG subsidy", "published_utc": datetime.datetime(2026, 8, 20),
               "body_text": "Petrobangla has requested Tk 27,000 crore for the first six months of FY27. "
                            "Petrobangla sought Tk 4,200 crore but the finance ministry released only Tk 2,500 crore. "
                            "Bangladesh Bank will inject fresh liquidity into National Bank Ltd to repay depositors."}

    def _raw(self, quote, **kw):
        r = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 27000.0, "is_cumulative": False,
             "event_date": None, "announced_date": None, "status": "ANNOUNCED", "channel": "CASH",
             "counterparty": "Petrobangla", "summary": "s", "evidence_quote": quote, "confidence": 0.8}
        r.update(kw)
        return r

    def test_a_request_is_rejected(self):
        ev, why = L.validate_event(self._raw(
            "Petrobangla has requested Tk 27,000 crore for the first six months of FY27."), self.ARTICLE)
        assert ev is None and "request" in why[0]

    def test_a_sentence_that_both_asks_and_pays_is_kept(self):
        ev, _ = L.validate_event(self._raw(
            "Petrobangla sought Tk 4,200 crore but the finance ministry released only Tk 2,500 crore.",
            amount_crore=2500.0, status="DISBURSED"), self.ARTICLE)
        assert ev is not None and ev["amount_crore"] == 2500.0

    def test_an_announced_injection_into_a_bank_is_still_an_event(self):
        ev, _ = L.validate_event(self._raw(
            "Bangladesh Bank will inject fresh liquidity into National Bank Ltd to repay depositors.",
            kind="BB_BANK_SUPPORT", amount_crore=None, channel="BB_CREDIT"), self.ARTICLE)
        assert ev is not None and ev["status"] == "ANNOUNCED"

    def test_a_future_subsidy_with_no_payment_yet_is_not_an_event(self):
        art = {**self.ARTICLE, "body_text": "The Finance Division will release Tk 3,000 crore for LNG next month, officials said."}
        ev, _ = L.validate_event(self._raw("The Finance Division will release Tk 3,000 crore for LNG next month, officials said.",
                                           amount_crore=3000.0), art)
        # An announced release is kept - as ANNOUNCED, which the page lists but never adds to a total.
        assert ev is not None and ev["status"] == "ANNOUNCED"

    def test_the_prompt_tells_the_model_the_same(self):
        assert "REQUEST" in T.EXTRACT_SYSTEM and "is NOT an event" in T.EXTRACT_SYSTEM


    def test_a_future_release_labelled_disbursed_by_a_reader_is_forced_to_announced(self):
        """A mislabelled status would add promised cash to 'injected this month'."""
        art = {**self.ARTICLE, "body_text": "The Finance Division will release Tk 3,000 crore for LNG next month, officials said."}
        ev, _ = L.validate_event(self._raw("The Finance Division will release Tk 3,000 crore for LNG next month, officials said.",
                                           amount_crore=3000.0, status="DISBURSED"), art)
        assert ev["status"] == "ANNOUNCED"
