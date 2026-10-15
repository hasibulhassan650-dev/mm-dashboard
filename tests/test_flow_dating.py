"""
tests/test_flow_dating.py — the daily cash ladder must be dated by SETTLEMENT.

This guards a real defect found on 2026-08-21. daily_net_flow was bucketing
coupons and maturities by scheduled_date (the contractual due date) instead of
payment_date (the working day BB actually settles). Because the Bangladesh
weekend is Friday-Saturday, 31.9% of coupon events (BDT 2.0tn) and 5.1% of
maturities (BDT 880bn) were shown 1-3 days EARLY — including on Fridays and
Saturdays, when no settlement can occur at all.

Real case: on 2026-07-26 the desk received 8 coupons totalling BDT 3,632mn.
The ladder showed BDT 72mn, having placed the rest on the 24th and 25th.

For a liquidity ladder this is not cosmetic: funding decisions are made off the
day cash lands, so being early leaves the desk short on the day it needs money.

Run with:  pytest tests/test_flow_dating.py -v
"""
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import calendar_utils
from engines.aggregation import build_daily_flows

# Bangladesh weekend is Fri/Sat. 2026-07-24 is a Friday, so a coupon due that
# day cannot settle until Sunday 2026-07-26.
DUE_FRIDAY = datetime.date(2026, 7, 24)
PAID_SUNDAY = datetime.date(2026, 7, 26)


def _coupon(amount, sched, paid):
    return {"isin": "BD_TEST", "scheduled_date": sched, "payment_date": paid,
            "amount_bdt_mill": amount}


def _maturity(amount, sched, paid):
    return {"isin": "BD_TEST2", "scheduled_date": sched, "payment_date": paid,
            "principal_bdt_mill": amount}


def _by_date(rows):
    return {r["flow_date"]: r for r in rows}


def test_coupon_lands_on_payment_date_not_due_date():
    """A coupon due on the weekend must appear on the day the cash arrives."""
    calendar_utils.load_holidays(set())
    rows = _by_date(build_daily_flows([_coupon(1000.0, DUE_FRIDAY, PAID_SUNDAY)], [], []))
    assert PAID_SUNDAY in rows, "coupon missing on its settlement date"
    assert rows[PAID_SUNDAY]["coupon_inflow_bdt_mill"] == 1000.0
    # And must NOT be sitting on the contractual due date.
    assert DUE_FRIDAY not in rows or rows[DUE_FRIDAY]["coupon_inflow_bdt_mill"] == 0.0, \
        "cash shown on the due date — the desk would plan funding a day early"


def test_maturity_lands_on_payment_date_not_due_date():
    calendar_utils.load_holidays(set())
    rows = _by_date(build_daily_flows([], [_maturity(5000.0, DUE_FRIDAY, PAID_SUNDAY)], []))
    assert rows[PAID_SUNDAY]["principal_inflow_bdt_mill"] == 5000.0
    assert DUE_FRIDAY not in rows or rows[DUE_FRIDAY]["principal_inflow_bdt_mill"] == 0.0


def test_no_cash_is_ever_dated_to_a_bangladesh_weekend():
    """The strongest invariant: banks are shut Fri/Sat, so no cash can land."""
    calendar_utils.load_holidays(set())
    events = [_coupon(100.0, datetime.date(2026, 7, 24), datetime.date(2026, 7, 26)),
              _coupon(200.0, datetime.date(2026, 7, 25), datetime.date(2026, 7, 26)),
              _coupon(300.0, datetime.date(2026, 7, 23), datetime.date(2026, 7, 23))]
    for r in build_daily_flows(events, [], []):
        if r["total_inflow_bdt_mill"] > 0:
            assert r["flow_date"].weekday() not in (4, 5), (
                f"cash dated to {r['flow_date']} (weekday {r['flow_date'].weekday()}) "
                "— a Bangladesh weekend, when nothing settles")


def test_totals_are_conserved_when_dates_roll():
    """Rolling must move cash, never create or destroy it."""
    calendar_utils.load_holidays(set())
    events = [_coupon(100.0, datetime.date(2026, 7, 24), datetime.date(2026, 7, 26)),
              _coupon(200.0, datetime.date(2026, 7, 25), datetime.date(2026, 7, 26))]
    rows = build_daily_flows(events, [], [])
    assert round(sum(r["coupon_inflow_bdt_mill"] for r in rows), 6) == 300.0


class TestTheLadderBooksCashNotPar:
    """A T-Bill with a face value of 3,000 crore does not drain 3,000 crore.

    Bills are discount instruments: on 07-Sep-2026 BB allotted 3,000.00 crore
    of 91-day bills and the market paid 2,937.48 crore. The 62.52 crore
    difference is the discount — it never left anyone's account. A premium
    re-issue goes the other way (20Y on 02-Sep-2026: face 3,500.00, cash
    3,689.28). Across 2026's 172 auctions the two differ on every single one,
    and booking face value overstated the drain on T-Bills alone by 15,861
    crore over the year.

    The face value still matters — it is what BB allotted and what matures —
    so both are kept. Only the ladder's cash line changes.
    """

    def _best(self, **kw):
        from engines.outflow import best_outflow
        return best_outflow(kw)

    def test_the_cash_paid_wins_over_the_face_value(self):
        assert self._best(outflow_status="CONFIRMED", sale_value_bdt_mill=29374.8,
                          accepted_amount_bdt_mill=30000.0,
                          offered_amount_bdt_mill=30000.0) == 29374.8

    def test_a_premium_reissue_drains_MORE_than_par(self):
        assert self._best(outflow_status="CONFIRMED", sale_value_bdt_mill=36892.8,
                          accepted_amount_bdt_mill=35000.0,
                          offered_amount_bdt_mill=35000.0) == 36892.8

    def test_par_is_used_when_bb_never_published_the_cash(self):
        """Older months were stored before the parser kept the sale value.

        Par is wrong by the discount, which is the best we have for those rows
        — but it must not be presented as if it were the cash figure, which is
        why the column stays NULL rather than being filled with the face value.
        """
        assert self._best(outflow_status="CONFIRMED", sale_value_bdt_mill=None,
                          accepted_amount_bdt_mill=30000.0,
                          offered_amount_bdt_mill=29000.0) == 30000.0

    def test_an_auction_that_has_not_happened_yet_uses_bbs_target(self):
        assert self._best(outflow_status="PLANNED", sale_value_bdt_mill=None,
                          accepted_amount_bdt_mill=None,
                          offered_amount_bdt_mill=30000.0) == 30000.0

    def test_a_planned_row_never_borrows_a_stale_result(self):
        """outflow_status is the gate: a PLANNED row with leftover result
        figures must still quote BB's target, or a re-homed auction (the
        23-May-2026 case) would book someone else's cash twice."""
        assert self._best(outflow_status="PLANNED", sale_value_bdt_mill=29374.8,
                          accepted_amount_bdt_mill=30000.0,
                          offered_amount_bdt_mill=25000.0) == 25000.0

    def test_the_engine_and_the_api_agree_on_the_precedence(self):
        """The api/ deploy boundary forces this precedence to exist twice.

        engines/outflow.py cannot be imported by the API, so schedule_logic
        repeats it in SQL. If the two ever drift, the same auction has two
        sizes depending on which page you open — so the order is asserted
        here, in both places, from one test.
        """
        import re
        from pathlib import Path
        sql = Path("api/routers/schedule_logic.py").read_text(encoding="utf-8")
        m = re.search(r'_AMT = "COALESCE\(([^)]*)\)"', sql)
        assert m, "schedule_logic no longer declares a single _AMT precedence"
        order = [c.strip() for c in m.group(1).split(",")]
        assert order[:3] == ["sale_value_bdt_mill", "accepted_amount_bdt_mill",
                             "offered_amount_bdt_mill"], order
        flows = Path("api/routers/flows.py").read_text(encoding="utf-8")
        assert "r.sale_value_bdt_mill or r.accepted_amount_bdt_mill" in flows
