"""Phase 1 of the Liquidity Watch overhaul: the wrong numbers.

Each test is a failure the code was measured to have (3-Nov-2026), named for the harm it did, not the function it calls.
"""
import datetime
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, NewsArticle
from engines import announce as A
from engines import liquidity_news as L
from engines import news_backfill as B
from engines import news_local as NLOC
from engines import news_rules as R
from engines import news_triage as T
import news_logic as NL

NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)
TODAY = datetime.date(2026, 10, 25)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _art(url="https://x/a", title="Govt releases Tk 3,000cr LNG subsidy", body="", pub=NOW):
    return {"url": url, "source": "dailystar_business", "title": title, "summary": "", "body_text": body,
            "published_utc": pub, "fetched_utc": pub}


def _ex(title, body):
    return [(e["kind"], e["amount_crore"], e["counterparty"]) for e in R.extract({"title": title, "body_text": body})]


class TestTheRuleReaderStoppedInventingEvents:
    def test_one_release_restated_in_later_sentences_is_one_event(self):
        """One LNG-subsidy article produced three SUBSIDY_RELEASE events and a month total of 5,100 for a 3,000 release."""
        got = _ex("Govt releases Tk 3,000cr LNG subsidy to Petrobangla",
                  "The Finance Division has released Tk 3,000 crore as LNG subsidy to Petrobangla on Sunday. "
                  "Petrobangla used the LNG subsidy released this week to pay Tk 1,200 crore to Chevron.")
        assert [g for g in got if g[0] == "SUBSIDY_RELEASE"] == [("SUBSIDY_RELEASE", 3000.0, "Petrobangla")]

    def test_the_headline_never_decides_a_sentences_kind_for_reserve_changes(self):
        """A CRR headline turned 'inflation eased' into a reserve cut, which always matters."""
        assert _ex("BB cuts CRR as inflation eased", "Inflation eased to 8.2 percent in October, the bureau said on Sunday.") == []

    @pytest.mark.parametrize("sentence", [
        "Petrobangla has requested the Finance Division to release Tk 27,000 crore as LNG subsidy for the year.",
        "The Finance Division plans to release Tk 4,000 crore as fuel subsidy to BPC next month.",
        "The ministry has decided to disburse Tk 2,000 crore of fertiliser subsidy to BCIC.",
    ])
    def test_a_request_or_plan_to_release_is_not_a_payment(self, sentence):
        """'release' and 'disburse' matched as completed verbs, so 'requested ... to release Tk 27,000 crore' was
        stored as DISBURSED, for the rule reader and for every model."""
        assert _ex("Subsidy", sentence) == []
        done, future = R._tense(sentence)
        assert not done

    def test_banks_borrowing_by_repo_is_not_government_borrowing(self):
        """GOVT_BORROW_BB always matters; interbank repo is the OMO market, which BB's own tables hold."""
        assert _ex("Banks borrow heavily", "Banks borrowed Tk 15,000 crore from Bangladesh Bank through repo in October.") == []

    def test_government_borrowing_from_the_central_bank_is_still_found(self):
        got = _ex("Govt borrows more", "The government borrowed Tk 9,000 crore from Bangladesh Bank through ways and means.")
        assert got and got[0][0] == "GOVT_BORROW_BB" and got[0][1] == 9000.0

    def test_the_month_may_is_not_a_hedge(self):
        """'a CRR cut from May 1' was dropped as if 'may' were a modal verb ('from June 1' was kept)."""
        got = _ex("CRR", "Bangladesh Bank cut the CRR by 50 basis points from May 1 for all scheduled banks.")
        assert got and got[0][0] == "RESERVE_REQ_CUT"
        assert not R.is_request("Bangladesh Bank cut the CRR from May 1")
        assert R.is_request("The rate may be cut next month")

    def test_a_total_of_is_a_period_total_not_a_new_payment(self):
        assert R._PERIOD.search("received a total of Tk 4,700 crore") and not R._STOCK.search("a total of Tk 4,700 crore")

    def test_tk_dot_does_not_split_a_sentence(self):
        got = _ex("Subsidy", "The Finance Division released Tk. 2,500 crore as fertiliser subsidy to BCIC on Monday afternoon.")
        assert got == [("SUBSIDY_RELEASE", 2500.0, "BCIC")]

    def test_a_crr_left_unchanged_is_not_a_cut(self):
        assert _ex("CRR", "Bangladesh Bank left the CRR unchanged at 4 percent and reduced nothing else on Sunday.") == []

    def test_the_reader_states_no_probability(self):
        """A regex has no confidence: it used to claim 0.7, above the 0.6 gate, which made its every event 'sure'."""
        assert all(e["confidence"] is None for e in R.extract(
            {"title": "x", "body_text": "The Finance Division released Tk. 2,500 crore as fertiliser subsidy to BCIC."}))


class TestAmounts:
    def test_lakh_is_a_hundredth_of_a_crore(self):
        """It was a tenth: 'Tk 5,000 lakh' (50 crore) was stored as 500, which crosses the 'matters' line."""
        assert L.amounts_crore("Tk 5,000 lakh") == [50.0] and L.amounts_crore("Tk 50 lakh") == [0.5]

    def test_a_hyphen_before_the_unit_is_read(self):
        assert L.amounts_crore("a Tk 27,000-crore subsidy") == [27000.0]

    def test_with_two_amounts_the_model_must_name_the_one_attached_to_the_act(self):
        """The quote holds the amount asked for and the amount paid; a figure merely being IN the quote proved nothing."""
        quote = "Petrobangla sought Tk 4,200 crore in April but the finance ministry released only Tk 2,500 crore."
        assert L.verify_amount(2500, quote, "SUBSIDY_RELEASE") == (2500.0, False)
        assert L.verify_amount(4200, quote, "SUBSIDY_RELEASE") == (None, True)


class TestAnUndatedArticleCannotCarryADistantDate:
    def test_the_window_anchors_on_the_fetch_time_without_a_publication_date(self):
        art = _art(pub=None)
        art["fetched_utc"] = NOW
        art["body_text"] = "The Finance Division released Tk 3,000 crore as LNG subsidy to Petrobangla on Sunday."
        raw = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 3000, "status": "DISBURSED",
               "event_date": "2019-01-01", "evidence_quote": art["body_text"], "confidence": 0.9}
        ev, why = L.validate_event(raw, art)
        assert ev["event_date"] is None and any("outside" in w for w in why)


class TestOneCashEventIsOneRow:
    def _ev(self, amount, cp, date=datetime.date(2026, 10, 20)):
        return {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": amount, "amount_unverified": False,
                "is_cumulative": False, "event_date": date, "announced_date": date, "status": "DISBURSED",
                "channel": "CASH", "counterparty": cp, "summary": "s", "evidence_quote": "q" * 30, "confidence": 0.9}

    def test_two_equal_payments_a_fortnight_apart_stay_two(self):
        """dedupe_key grouped by month, so two equal payments to the same party 11+ days apart became one."""
        s = _mem()
        L.merge_or_create(s, self._ev(1000, "Petrobangla", datetime.date(2026, 10, 2)), _art("https://x/1"), NOW)
        L.merge_or_create(s, self._ev(1000, "Petrobangla", datetime.date(2026, 10, 20)), _art("https://x/2"), NOW)
        assert s.query(LiquidityEvent).count() == 2

    def test_an_amountless_mention_from_another_article_does_not_merge_into_a_payment(self):
        """A missing amount fell through to 'match', so a vague mention of a different event joined a 3,000 crore row."""
        s = _mem()
        L.merge_or_create(s, self._ev(3000, "Petrobangla"), _art("https://x/1"), NOW)
        L.merge_or_create(s, self._ev(None, None), _art("https://x/2"), NOW)
        assert s.query(LiquidityEvent).count() == 2

    def test_the_same_event_in_two_outlets_is_one_row_with_two_sources(self):
        s = _mem()
        L.merge_or_create(s, self._ev(3000, "Petrobangla"), _art("https://x/1"), NOW)
        row, how = L.merge_or_create(s, self._ev(3010, "Petrobangla"), _art("https://x/2"), NOW)
        assert how == "merged" and len(json.loads(row.sources_json)) == 2 and s.query(LiquidityEvent).count() == 1


class TestAnAiReadingConfirmsOrReplacesTheRuleReaders:
    def _rule_lead(self, s, url="https://x/a", kind="SUBSIDY_RELEASE", amount=3000, cp="Petrobangla"):
        ev = {"kind": kind, "direction": L.KINDS[kind], "amount_crore": amount, "amount_unverified": False,
              "is_cumulative": False, "event_date": None, "announced_date": datetime.date(2026, 10, 20),
              "status": "DISBURSED", "channel": "CASH", "counterparty": cp, "summary": "s",
              "evidence_quote": "q" * 30, "confidence": None}
        row, _ = L.merge_or_create(s, ev, _art(url), NOW, verified_by="rules")
        return row

    def test_an_agreeing_ai_reading_confirms_the_lead_without_a_duplicate(self):
        s = _mem()
        lead = self._rule_lead(s)
        assert lead.verified_by == "rules"
        ev = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 3000, "amount_unverified": False,
              "is_cumulative": False, "event_date": None, "announced_date": datetime.date(2026, 10, 20),
              "status": "DISBURSED", "channel": "CASH", "counterparty": "Petrobangla", "summary": "AI",
              "evidence_quote": "q" * 30, "confidence": 0.9}
        row, how = L.merge_or_create(s, ev, _art("https://x/a"), NOW, verified_by="llm")
        assert how == "merged" and row.id == lead.id and s.query(LiquidityEvent).count() == 1
        assert row.verified_by == "llm" and row.confirmed_utc == NOW and row.confidence == 0.9

    def test_an_ai_reading_that_reports_something_else_retires_the_leads_from_that_article(self):
        """The rule reader's event usually has the WRONG kind, so it never merges with the model's: without superseding
        by article it lived on beside the model's event, announced and added up twice."""
        s = _mem()
        lead = self._rule_lead(s, kind="ARREARS_CASH", amount=1200)
        n = L.supersede_rule_reads(s, "https://x/a", kept_ids=[999], now=NOW)
        s.commit()
        assert n == 1 and s.get(LiquidityEvent, lead.id).superseded_utc == NOW

    def test_a_lead_that_cites_other_articles_only_loses_this_one(self):
        s = _mem()
        lead = self._rule_lead(s)
        L.merge_or_create(s, {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 3000, "amount_unverified": False,
                              "is_cumulative": False, "event_date": None, "announced_date": datetime.date(2026, 10, 20),
                              "status": "DISBURSED", "channel": "CASH", "counterparty": "Petrobangla", "summary": "s",
                              "evidence_quote": "q" * 30, "confidence": None}, _art("https://x/b"), NOW, verified_by="rules")
        L.supersede_rule_reads(s, "https://x/a", kept_ids=[], now=NOW)
        row = s.get(LiquidityEvent, lead.id)
        assert row.superseded_utc is None and [x["url"] for x in json.loads(row.sources_json)] == ["https://x/b"]

    def test_the_triage_run_supersedes_after_an_ai_read(self):
        s = _mem()
        lead = self._rule_lead(s, url="https://x/a", kind="ARREARS_CASH", amount=1200)
        stats = {"events_created": 0, "events_merged": 0, "events_rejected": 0, "reasons": []}
        T._store_events(s, [], _art("https://x/a"), NOW, stats, "llm", "https://x/a")
        assert s.get(LiquidityEvent, lead.id).superseded_utc == NOW and stats["rule_leads_superseded"] == 1


class TestUnconfirmedLeadsAreNotFigures:
    def _lead_and_confirmed(self):
        s = _mem()
        for i, (vb, amt) in enumerate((("rules", 3000), ("llm", 500))):
            s.add(LiquidityEvent(dedupe_key=f"k{i}", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=amt,
                                 is_cumulative=False, announced_date=TODAY, status="DISBURSED", channel="CASH",
                                 evidence_quote="q" * 30, sources_json="[]", matters=True, first_seen_utc=NOW,
                                 verified_by=vb, updated_utc=NOW))
        s.commit()
        return s

    def test_only_the_confirmed_event_is_in_the_totals(self):
        blk = NL.news_block(self._lead_and_confirmed(), today=TODAY)
        assert blk["totals"]["month_inject_crore"] == 500.0 and len(blk["unconfirmed"]) == 1
        assert blk["unconfirmed"][0]["amount_crore"] == 3000

    def test_the_day_page_lists_confirmed_events_only(self):
        assert [e["amount_crore"] for e in NL.events_on(self._lead_and_confirmed(), TODAY)] == [500.0]

    def test_the_announcer_skips_leads_and_announces_a_confirmed_event_even_days_later(self):
        s = self._lead_and_confirmed()
        ids = [r["serial"] for r in A.pending(s, now=NOW)]
        assert ids == ["ev-2"], "the rule-read lead (ev-1) is not announced"
        # confirmed on day 6: the age window runs from confirmation, not from first sight
        row = s.get(LiquidityEvent, 1)
        row.verified_by, row.confirmed_utc = "llm", NOW + datetime.timedelta(days=6)
        s.commit()
        assert "ev-1" in [r["serial"] for r in A.pending(s, now=NOW + datetime.timedelta(days=6))]

    def test_the_confirmed_predicate_is_identical_in_api_and_engines(self):
        """api/ cannot import engines/, so the predicate is copied. Two copies that drift is one that is wrong."""
        assert NL.CONFIRMED_SQL == L.CONFIRMED_SQL


class TestTheBacklogOfLeadsIsWorkedOffByCount:
    def test_an_old_lead_is_still_queued_for_the_ai_reader(self):
        """The 3-day window left a lead older than 3 days unconfirmed for ever."""
        s = _mem()
        old = NOW - datetime.timedelta(days=30)
        s.add(NewsArticle(url="https://x/old", source="s", title="t", fetched_utc=old, triage_status="extracted",
                          triage_model="rules-v1", body_text="b"))
        s.add(LiquidityEvent(dedupe_key="k", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=1,
                             evidence_quote="q" * 30, sources_json=json.dumps([{"url": "https://x/old"}]),
                             matters=True, first_seen_utc=old, verified_by="rules", updated_utc=old))
        s.commit()
        assert NLOC.lift_rule_reads(s, now=NOW) == 1
        assert s.query(NewsArticle).one().triage_status == "untriaged"

    def test_it_is_bounded_so_a_big_backlog_cannot_burn_the_daily_cap(self):
        s = _mem()
        for i in range(50):
            s.add(NewsArticle(url=f"https://x/{i}", source="s", title="t", fetched_utc=NOW, triage_status="extracted",
                              triage_model="rules-v1", body_text="b"))
        s.commit()
        assert NLOC.lift_rule_reads(s, now=NOW, max_articles=30) == 30


class TestABackfillOnlySilencesItsOwnEvents:
    def test_a_live_event_that_arrived_before_the_run_is_still_announced(self):
        """mark_history marked EVERY un-notified event as announced, which silenced a live event for ever."""
        s = _mem()
        start = NOW
        for i, seen in enumerate((start - datetime.timedelta(hours=3), start)):
            s.add(LiquidityEvent(dedupe_key=f"k{i}", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=1000,
                                 evidence_quote="q" * 30, sources_json="[]", matters=True, first_seen_utc=seen,
                                 verified_by="llm", updated_utc=seen))
        s.commit()
        assert B.mark_history(s, start) == 1
        assert s.query(LiquidityEvent).filter_by(dedupe_key="k0").one().notified_utc is None
        assert s.query(LiquidityEvent).filter_by(dedupe_key="k1").one().notified_utc == start
