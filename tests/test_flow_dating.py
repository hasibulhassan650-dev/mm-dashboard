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
