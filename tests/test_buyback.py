"""Bangladesh's first bond buyback, and the 1,716.14 crore it put into the market.

On 08-Oct-2026 BB announced a buyback under the government's Liability
Management Operation: ISIN BD0926191026, the 2-year bond issued 06-Nov-2024,
56 bids for 2,016.14 crore of which 1,716.14 crore was accepted. The press
release — in Bengali, and the only published account — states neither the
settlement date nor the price.

Both were established from a second BB publication rather than assumed: GSOM's
daily outstanding for that ISIN fell from 66,665.80 to 49,504.40 million
between 11 and 12 October. That is 17,161.40 million, exactly the accepted
amount, which proves the figure is FACE value and that it settled on 12-Oct.
Until this was recorded the ladder showed nothing at all on 12-Oct for it —
a 1,716 crore injection missing from the day it happened.

What is NOT known is the price. The bond carries a 12.3% coupon and was quoted
at a 7.96% yield, so it traded at a clear premium and BB paid more than par.
By how much is unpublished, so `cash_paid_crore` stays NULL and the ladder
falls back to face with that stated — the same precedence as an auction whose
sale value BB did not print. These tests pin that choice down, because the
tempting alternative is to derive a price from the yield, and a derived number
presented as a fact is the one error this project will not make.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import BuybackEvent, MtmSnapshot
from engines.aggregation import build_daily_flows, buyback_amount

D = datetime.date
SETTLE = D(2026, 10, 12)
REAL = {"isin": "BD0926191026", "settlement_date": SETTLE,
        "accepted_face_crore": 1716.14, "cash_paid_crore": None}


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


class TestTheAmountTheLadderUses:
    def test_face_value_when_bb_publishes_no_price(self):
        # 1,716.14 crore = 17,161.40 million.
        assert buyback_amount(REAL) == 17161.40

    def test_the_cash_wins_when_bb_does_publish_it(self):
        assert buyback_amount({**REAL, "cash_paid_crore": 1785.03}) == 17850.30

    def test_a_missing_face_is_zero_not_a_crash(self):
        assert buyback_amount({"settlement_date": SETTLE}) == 0.0

    def test_no_price_is_ever_derived_from_the_yield(self):
        """The bond traded at a premium, so face understates the cash. The
        understatement is accepted and labelled; it is not modelled away."""
        assert buyback_amount(REAL) == 17161.40, \
            "face value, unchanged — any other number here was invented"


class TestItReachesTheLadder:
    def test_it_appears_on_its_settlement_day(self):
        rows = build_daily_flows([], [], [], SETTLE, SETTLE, buyback_events=[REAL])
        day = next(r for r in rows if r["flow_date"] == SETTLE)
        assert day["buyback_inflow_bdt_mill"] == 17161.40
        assert day["total_inflow_bdt_mill"] == 17161.40

    def test_a_day_with_no_other_event_still_gets_a_row(self):
        """Before this, a buyback on a quiet day produced no row at all — the
        ladder simply had nothing where 1,716 crore arrived."""
        rows = build_daily_flows([], [], [], None, None, buyback_events=[REAL])
        assert [r["flow_date"] for r in rows] == [SETTLE]

    def test_it_is_not_folded_into_principal_inflow(self):
        """principal_inflow reconciles against maturity_events. A buyback is
        not a maturity — the bond goes on maturing, for less — so folding it in
        would make that reconciliation fail for ever."""
        rows = build_daily_flows([], [], [], SETTLE, SETTLE, buyback_events=[REAL])
        day = rows[0]
        assert day["principal_inflow_bdt_mill"] == 0.0
        assert day["coupon_inflow_bdt_mill"] == 0.0

    def test_it_adds_to_a_day_that_already_had_flows(self):
        coupon = {"scheduled_date": SETTLE, "payment_date": SETTLE,
                  "amount_bdt_mill": 500.0, "isin": "X", "data_quality": "OK"}
        rows = build_daily_flows([coupon], [], [], SETTLE, SETTLE, buyback_events=[REAL])
        day = rows[0]
        assert day["total_inflow_bdt_mill"] == 17661.40
        assert day["buyback_inflow_bdt_mill"] == 17161.40

    def test_it_reduces_net_borrowing_like_any_other_inflow(self):
        auction = {"settlement_date": SETTLE, "offered_amount_bdt_mill": 30000.0,
                   "accepted_amount_bdt_mill": 30000.0, "sale_value_bdt_mill": 29000.0,
                   "outflow_status": "CONFIRMED", "security_type": "T_BILL",
                   "data_quality": "OK"}
        with_bb = build_daily_flows([], [], [auction], SETTLE, SETTLE,
                                    buyback_events=[REAL])[0]
        without = build_daily_flows([], [], [auction], SETTLE, SETTLE)[0]
        assert without["net_borrowing_bdt_mill"] - with_bb["net_borrowing_bdt_mill"] \
            == 17161.40


class TestTheGuardianChecksItAgainstGSOM:
    """BB states the same fact twice. Two statements are the strongest check
    available, and here they are the only reason we know the settlement date."""

    def _issues(self, monkeypatch, face, pre=66665.80, post=49504.40):
        import validate
        eng, s = _mem()
        s.add(BuybackEvent(isin="BD0926191026", settlement_date=SETTLE,
                           accepted_face_crore=face, offered_face_crore=2016.14,
                           bids_received_count=56))
        for d, o in ((D(2026, 10, 11), pre), (D(2026, 10, 12), post)):
            s.add(MtmSnapshot(isin="BD0926191026", settlement_date=d,
                              outstanding_bdt_mill=o, market_yield_pct=7.96,
                              market_price=104.2, data_quality="OK"))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check()
        return [i for i in rep["issues"] + rep["waiting"] if i.startswith("buyback")]

    def test_the_real_figures_reconcile_silently(self, monkeypatch):
        assert self._issues(monkeypatch, 1716.14) == []

    def test_a_misread_amount_is_caught(self, monkeypatch):
        """1,716.14 misread as 1,761.14 — two digits transposed, 45 crore out."""
        found = self._issues(monkeypatch, 1761.14)
        assert found, "a transposed figure moved 45 crore and nothing objected"
        assert "GSOM" in found[0]

    def test_a_crore_million_mixup_is_caught(self, monkeypatch):
        found = self._issues(monkeypatch, 17161.40)
        assert found and "misread" in found[0]

    def test_it_says_so_when_gsom_cannot_confirm(self, monkeypatch):
        """No outstanding either side of the date: unprovable, not wrong. It
        waits rather than failing a build, but it never goes quiet."""
        import validate
        eng, s = _mem()
        s.add(BuybackEvent(isin="BD0926191026", settlement_date=SETTLE,
                           accepted_face_crore=1716.14))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check()
        assert any(i.startswith("buyback") and "cannot be checked" in i
                   for i in rep["waiting"]), rep["waiting"]
