"""Blind spots a one-line mutation audit found in the money-critical date/amount logic (53 mutants: these survived).

Why: coupon and principal dates drive daily_net_flow, the liquidity ladder a fund manager trades on. Each assertion names the
harm if the line it guards changes: a payment on a closed day, a short coupon paid as a full one, a final coupon that goes missing."""
import datetime

import calendar_utils
from engines.inflow import generate_coupon_schedule, generate_maturity_event

D = datetime.date


def setup_function(_):
    calendar_utils.load_holidays(set(), set())          # weekends (Fri/Sat) only: no gazette needed for these cases


def _sec(**kw):
    base = {"isin": "BD0000000001", "security_type": "T_BOND", "coupon_frequency": "HFLY", "coupon_rate_pct": 10.0,
            "outstanding_bdt_mill": 1000.0, "issue_date": D(2026, 1, 1), "maturity_date": D(2031, 1, 1),
            "settlement_date": D(2026, 11, 1)}
    base.update(kw)
    return base


def test_principal_due_on_a_friday_is_paid_on_the_next_working_day_sunday():
    """Principal falling on the closed Friday must roll to Sunday (2 days). Paying it on the raw date puts BDT crores of
    redemption on a day nothing settles."""
    ev = generate_maturity_event(_sec(maturity_date=D(2026, 12, 4)))              # 4-Dec-2026 is a Friday
    assert D(2026, 12, 4).weekday() == 4
    assert ev["payment_date"] == D(2026, 12, 6) and ev["roll_days"] == 2 and ev["scheduled_date"] == D(2026, 12, 4)


def test_a_dust_outstanding_produces_no_maturity_event():
    assert generate_maturity_event(_sec(outstanding_bdt_mill=0.0)) is None
    assert generate_maturity_event(_sec(outstanding_bdt_mill=None)) is None


def test_a_bond_maturing_inside_its_first_period_pays_one_short_prorated_coupon():
    """Issued 1-Jan, matures 15-Mar (73 days): ONE coupon, prorated by actual days (ACT/365) - not a full half-year coupon."""
    ev = generate_coupon_schedule(_sec(maturity_date=D(2026, 3, 15), issue_date=D(2026, 1, 1)))
    assert len(ev) == 1 and ev[0]["is_short_coupon"] and ev[0]["period_days_actual"] == 73
    assert abs(ev[0]["amount_bdt_mill"] - 1000.0 * 0.10 * 73 / 365) < 1e-3
    assert ev[0]["amount_bdt_mill"] < 1000.0 * 0.10 / 2 / 2


def test_a_quarterly_first_coupon_of_exactly_ninety_days_is_a_full_coupon_not_a_short_one():
    ev = generate_coupon_schedule(_sec(coupon_frequency="QRTY", issue_date=D(2026, 1, 1), maturity_date=D(2026, 4, 1)))
    assert (D(2026, 4, 1) - D(2026, 1, 1)).days == 90
    assert len(ev) == 1 and not ev[0]["is_short_coupon"]
    assert abs(ev[0]["amount_bdt_mill"] - 1000.0 * 0.10 / 4) < 1e-6


def test_the_final_coupon_is_paid_on_the_maturity_date_even_when_the_anniversary_is_a_day_off():
    """Issue 31-Aug, maturity 28-Feb: the 6-month anniversary lands on 28-Feb already, but issue 1-Sep + 6m = 1-Mar is a day AFTER
    maturity. The last coupon must still exist, on the maturity date, once."""
    ev = generate_coupon_schedule(_sec(issue_date=D(2025, 9, 1), maturity_date=D(2026, 2, 28)))
    dates = [e["scheduled_date"] for e in ev]
    assert dates and dates[-1] == D(2026, 2, 28) and dates.count(D(2026, 2, 28)) == 1


def test_coupon_dates_never_drift_from_the_issue_day_of_month():
    ev = generate_coupon_schedule(_sec(issue_date=D(2023, 1, 31), maturity_date=D(2026, 1, 31)))
    assert D(2024, 1, 31) in [e["scheduled_date"] for e in ev]          # 31-Jul -> 31-Jan again, not 28/29-Jan carried forward
