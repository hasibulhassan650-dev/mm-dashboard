"""The Liquidity Watch must work with no key, so the free reader has to be trustworthy.

A key is a bill, and the desk asked for cost-free. The rule reader therefore carries the whole
job when there is no key - and the checks after it (quote in the article, amount re-read) cannot
catch a MISCLASSIFIED but real sentence. So every case here is a sentence from a real article
(or a close copy) that a plausible-looking rule would get wrong: a request read as a release, a
stock read as a flow, a running total added to a month, the wrong one of two amounts.
"""
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
from engines import announce as A
from engines import liquidity_news as L
from engines import news_rules as R
from engines import news_triage as T
import news_logic as NL

NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)
TODAY = datetime.date(2026, 10, 25)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _one(title, body):
    ev = R.extract({"title": title, "body_text": body})
    assert len(ev) == 1, ev
    return ev[0]


class TestWhatItFinds:
    def test_a_period_total_of_subsidy_is_found_and_marked_cumulative(self):
        """FE, FY27: 'disbursed Tk 47 billion' beside 'Tk 110 billion' (the allocation) and
        'Tk 5.0 billion' (last year). The amount is the one attached to the verb."""
        e = _one("Nearly 43pc of annual LNG subsidy spent in just 1.5 months",
                 "Until mid-August, sources have said, the Finance Division had disbursed Tk 47 billion "
                 "(42.73 per cent of FY27 allocation of Tk 110 billion) in favour of Energy Division-over "
                 "nine times higher than the amount of Tk 5.0 billion disbursed in same period in the last fiscal year.")
        assert (e["kind"], e["amount_crore"], e["is_cumulative"], e["status"]) == ("SUBSIDY_RELEASE", 4700.0, True, "DISBURSED")
        assert e["counterparty"] == "Energy Division"

    def test_government_borrowing_from_bb_takes_the_amount_before_from_the_central_bank(self):
        """The sentence also says Tk23,227 crore from commercial lenders - which is NOT BB."""
        e = _one("Govt borrows Tk24,245cr from banks in Q1 of FY27",
                 "During these three months, public borrowing comprised Tk1,017 crore drawn directly from "
                 "the central bank alongside Tk23,227 crore secured from commercial lenders.")
        assert e["kind"] == "GOVT_BORROW_BB" and e["amount_crore"] == 1017.0 and e["is_cumulative"]

    def test_a_refinance_facility_increase_takes_the_reallocated_amount(self):
        e = _one("BB expands export pre-shipment refinance fund to Tk7,000cr",
                 "To facilitate the expansion, the central bank reallocated Tk2,000 crore from its Tk10,000-crore "
                 "Export Facilitation Pre-finance Fund (EFPF) into the pre-shipment scheme, bringing the remaining "
                 "balance of the EFPF down to Tk3,000 crore.")
        assert (e["kind"], e["amount_crore"], e["status"]) == ("BB_REFINANCE", 2000.0, "APPROVED")

    def test_a_partial_release_takes_what_was_released_not_what_was_asked(self):
        e = _one("Govt under heavy pressure from LNG subsidies",
                 "Petrobangla sought Tk 4,200 crore in April but the finance ministry released only Tk 2,500 crore, "
                 "which Petrobangla used to cover part of the import costs.")
        assert e["amount_crore"] == 2500.0 and e["counterparty"] == "Petrobangla"

    def test_one_months_payment_is_not_cumulative_but_two_months_is(self):
        one = _one("LNG subsidy", "The Finance Division released Tk 2,500 crore in April for the LNG subsidy to Petrobangla.")
        two = _one("LNG subsidy", "The Finance Division released Tk 3,000 crore in July and August for the LNG subsidy to Petrobangla.")
        assert one["is_cumulative"] is False and two["is_cumulative"] is True

    def test_an_announced_injection_into_a_bank_matters_without_an_amount(self):
        e = _one("BB to inject more liquidity into National Bank",
                 "Bangladesh Bank (BB) will inject fresh liquidity into the troubled National Bank Ltd (NBL) to help "
                 "the lender repay its depositors.")
        assert (e["kind"], e["amount_crore"], e["status"], e["counterparty"]) == (
            "BB_BANK_SUPPORT", None, "ANNOUNCED", "National Bank Ltd")
        assert L.matters({**e, "kind": e["kind"]})

    def test_a_crr_cut_is_found_and_has_no_amount(self):
        e = _one("BB cuts CRR", "Bangladesh Bank has reduced the cash reserve ratio (CRR) of banks by 0.50 percent to boost liquidity flow in the banks.")
        assert (e["kind"], e["amount_crore"], e["channel"]) == ("RESERVE_REQ_CUT", None, "REGULATORY")

    def test_a_crr_hike_is_the_opposite_direction(self):
        e = _one("BB raises CRR", "Bangladesh Bank has raised the cash reserve ratio of banks to absorb excess liquidity.")
        assert e["kind"] == "RESERVE_REQ_HIKE" and e["direction"] == "ABSORB"


class TestWhatItMustNotInvent:
    def test_a_request_is_not_a_release(self):
        assert R.extract({"title": "Petrobangla subsidy", "body_text":
                          "Petrobangla has requested Tk 27,000 crore in subsidies for the first six months of FY2026-27."}) == []

    def test_a_regulators_projection_is_not_a_release(self):
        assert R.extract({"title": "BERC", "body_text":
                          "BERC projects that the government will still need to provide around Tk 41,000 crore in "
                          "subsidy to BPDB in the upcoming fiscal year."}) == []

    def test_a_demand_for_subsidy_is_not_an_event(self):
        assert R.extract({"title": "Power", "body_text":
                          "The Power Division has sought a total Tk43,904.43 crore in additional subsidy for FY27 for imports and rental plants."}) == []

    def test_a_stock_is_not_new_cash(self):
        """'As of June BB had provided Tk 76,000 crore' restates a stock; reading it as a
        flow would add 76,000 crore to a month."""
        assert R.extract({"title": "BB", "body_text":
                          "As of early June 2026, BB had provided Tk 76,000 crore ($6.2 billion) as liquidity support to "
                          "weak banks, said Finance Minister Amir Khosru Mahmud Chowdhury in parliament earlier."}) == []

    def test_a_plan_to_issue_bonds_is_not_an_event(self):
        assert R.extract({"title": "Special bonds", "body_text":
                          "Government plans to issue special bonds worth Tk 26,000 crore to clear arrears to power producers."}) == []

    def test_cricket_is_nothing(self):
        assert R.extract({"title": "Cricket", "body_text":
                          "Bangladesh beat Sri Lanka by five wickets in Dhaka on Sunday evening in the second match."}) == []

    def test_a_future_subsidy_is_not_an_event(self):
        """'will' counts only where the announcement itself is the news (a CRR change, BB lending)."""
        assert R.extract({"title": "LNG", "body_text":
                          "The Finance Division will release Tk 3,000 crore for the LNG subsidy to Petrobangla next month."}) == []

    def test_several_amounts_none_attached_to_the_act_gives_no_amount_not_a_guess(self):
        e = R.extract({"title": "Subsidy", "body_text":
                       "Subsidy for LNG import by Petrobangla was Tk 2,500 crore against Tk 4,200 crore needed, "
                       "the finance ministry paid part of it."})
        assert all(x["amount_crore"] is None for x in e)


class TestItIsHeldToTheSameChecksAsClaude:
    def test_every_rule_event_passes_through_validate_event(self):
        art = {"title": "LNG subsidy", "published_utc": datetime.datetime(2026, 10, 20),
               "body_text": "The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla for September."}
        ev = R.extract(art)
        assert len(ev) == 1
        ok, why = L.validate_event(ev[0], art)
        assert ok and ok["amount_crore"] == 4000.0

    def test_the_quote_is_always_a_sentence_of_the_article(self):
        art = {"title": "LNG subsidy", "published_utc": datetime.datetime(2026, 10, 20),
               "body_text": "Officials spoke on Monday. The Finance Division has released Tk 4,000 crore as LNG subsidy to "
                            "Petrobangla for September. More follows."}
        assert R.extract(art)[0]["evidence_quote"] in art["body_text"]


LNG = ("The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla for the month of September, "
       "officials said. The money will be used to pay import bills.")


def _article(s, url="https://x.example/lng", body=LNG, title="Govt releases Tk 4,000 crore LNG subsidy"):
    s.add(NewsArticle(url=url, source="tbs_economy", title=title, summary="", body_text=body,
                      published_utc=datetime.datetime(2026, 10, 20, 8), fetched_utc=NOW, triage_status="untriaged"))
    s.commit()


class TestNoKeyMeansNoBillNotNoReading:
    def test_with_no_key_the_article_is_read_and_an_event_stored(self):
        s = _mem()
        _article(s)
        st = T.triage_pending(s, None, NOW, fetch_body=lambda u: None)
        assert st["mode"] == "rules" and st["extracted"] == 1 and st["untriaged_left"] == 0
        e = s.query(LiquidityEvent).one()
        assert e.amount_crore == 4000.0 and e.verified_by == "rules" and e.matters
        a = s.query(NewsArticle).one()
        assert a.triage_status == "extracted" and a.triage_model == R.RULES_MODEL

    def test_the_rule_reader_makes_no_model_call_and_costs_nothing(self):
        s = _mem()
        _article(s)
        T.triage_pending(s, None, NOW, fetch_body=lambda u: None)
        assert T.calls_today(s, NOW) == 0, "rule reads must not count against the paid daily ceiling"

    def test_a_rule_read_event_is_announced_with_a_label_saying_so(self):
        s = _mem()
        _article(s)
        T.triage_pending(s, None, NOW, fetch_body=lambda u: None)

        class Run:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd, **kw):
                self.calls.append(cmd)

                class R_:
                    returncode, stdout, stderr = 0, "[]", ""
                return R_()
        r = Run()
        A.publish(s, runner=r, now=NOW)
        title = [c[c.index("--title") + 1] for c in r.calls if c[1:3] == ["issue", "create"]][0]
        assert title.startswith("Liquidity news (rule-read):")

    def test_a_key_arriving_later_makes_claude_reread_what_the_rules_read(self):
        from engines import news_backfill as B
        s = _mem()
        _article(s)
        T.triage_pending(s, None, NOW, fetch_body=lambda u: None)
        assert s.query(NewsArticle).one().triage_status == "extracted"

        class C:
            def __init__(self):
                self.messages, self.calls = self, []

            def create(self, **kw):
                self.calls.append(kw["model"])
                from types import SimpleNamespace as NS
                txt = json.dumps({"is_candidate": True}) if kw["model"] == T.SCREEN_MODEL else json.dumps({"events": []})
                return NS(stop_reason="end_turn", content=[NS(type="text", text=txt)])
        c = C()
        st = B.backfill(s, c, datetime.date(2026, 7, 1), now=NOW, queries=[], fetch_body=lambda u: LNG,
                        discover_fn=lambda *a: [])
        assert st["reread_by_claude"] == 1 and T.SCREEN_MODEL in c.calls


class TestRunningTotalsAreNeverAdded:
    def _ev(self, s, amount, cum, when, key):
        s.add(LiquidityEvent(dedupe_key=key, kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=amount,
                             is_cumulative=cum, status="DISBURSED", counterparty="Energy Division", summary="s",
                             evidence_quote="q", sources_json="[]", confidence=0.7, matters=True, event_date=when,
                             first_seen_utc=NOW))
        s.commit()

    def test_a_cumulative_figure_is_listed_but_not_summed_into_the_month_or_the_year(self):
        """'Tk 47bn until mid-August' then 'Tk 70bn until September' summed would count the
        first 47bn twice."""
        s = _mem()
        self._ev(s, 4700.0, True, datetime.date(2026, 8, 15), "a")
        self._ev(s, 7000.0, True, datetime.date(2026, 10, 1), "b")
        self._ev(s, 2500.0, False, datetime.date(2026, 10, 12), "c")
        b = NL.news_block(s, today=TODAY)
        assert b["totals"]["fy_inject_crore"] == 2500.0 and b["totals"]["month_inject_crore"] == 2500.0
        assert b["totals"]["events_cumulative"] == 2 and b["count"] == 3

    def test_a_cumulative_and_a_one_off_of_similar_size_do_not_merge(self):
        s = _mem()
        a = {"url": "https://x/1", "source": "s", "title": "t", "published_utc": datetime.datetime(2026, 10, 20)}
        base = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 4000.0, "event_date": datetime.date(2026, 10, 19),
                "announced_date": None, "status": "DISBURSED", "channel": "CASH", "counterparty": "Petrobangla",
                "summary": "s", "evidence_quote": "q", "confidence": 0.9, "amount_unverified": False}
        L.merge_or_create(s, {**base, "is_cumulative": True}, a, NOW)
        _, how = L.merge_or_create(s, {**base, "is_cumulative": False}, {**a, "url": "https://x/2"}, NOW)
        assert how == "created"

    def test_the_reader_is_named_on_the_feed(self):
        s = _mem()
        _article(s)
        T.triage_pending(s, None, NOW, fetch_body=lambda u: None)
        assert NL.news_block(s, today=TODAY)["health"]["reader"] == "rules"
