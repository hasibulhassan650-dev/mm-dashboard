"""
tests/test_coupon.py — coupon amount + schedule guards against the LIVE engine
(engines/inflow.py). Ported from the retired engine/ package tests.

WHY these matter: coupon inflows feed daily_net_flow and the liquidity
forecast a fund manager trades on. A formula regression (day-count amounts,
drifting day-of-month, rolled-anchor corruption) silently mis-states BDT
crores of daily inflow — these tests exist to make that loud.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from engines.inflow import (
    _coupon_amount_approx, _coupon_amount_act365, _divisor,
    generate_coupon_schedule,
)


# ── Formula correctness ───────────────────────────────────────────────────────

class TestCouponFormulas:
    def test_hfly_is_face_times_rate_over_two(self):
        # Market convention: 2000mn × 11% / 2 = 110mn — never day-count adjusted
        assert _coupon_amount_approx(2000.00, 11.00, "HFLY") == 110.00

    def test_qrty_is_face_times_rate_over_four(self):
        # 5000mn × 11.72% / 4 = 146.5mn
        assert abs(_coupon_amount_approx(5000.00, 11.72, "QRTY") - 146.50) < 1e-6

    def test_the_4621_regression(self):
        # BD0928221052: face×rate/2 = 4621.38, NOT the day-count 4608.xx a past
        # bug produced. Any change to this number mis-states real inflow.
        assert round(_coupon_amount_approx(81076.85, 11.40, "HFLY"), 2) == 4621.38

    def test_act365_prorates_short_first_coupon(self):
        # Bond issued mid-period: 2000 × 11% × 182/365 = 109.70
        expected = 2000.0 * 0.11 * 182 / 365
        assert abs(_coupon_amount_act365(2000.0, 11.00, 182) - expected) < 1e-9

    def test_divisor(self):
        assert _divisor("HFLY") == 2
        assert _divisor("QRTY") == 4


# ── Schedule generation ───────────────────────────────────────────────────────

def _bond_row(**over):
    row = {
        "isin":                 "BD0900TEST00",
        "security_type":        "T_BOND",
        "coupon_frequency":     "HFLY",
        "issue_date":           date(2023, 6, 14),
        "maturity_date":        date(2028, 6, 14),
        "coupon_rate_pct":      11.40,
        "outstanding_bdt_mill": 10000.0,
        "settlement_date":      date(2026, 7, 1),
        "next_coupon_date":     None,
        "last_coupon_date":     None,
    }
    row.update(over)
    return row


class TestCouponSchedule:
    def test_tbill_has_no_coupons(self):
        assert generate_coupon_schedule(_bond_row(security_type="T_BILL")) == []

    def test_none_frequency_has_no_coupons(self):
        assert generate_coupon_schedule(_bond_row(coupon_frequency="NONE")) == []

    def test_missing_essentials_yield_empty_not_garbage(self):
        # Failing loud-and-empty beats generating events from partial data
        assert generate_coupon_schedule(_bond_row(coupon_rate_pct=None)) == []
        assert generate_coupon_schedule(_bond_row(outstanding_bdt_mill=None)) == []

    def test_day_of_month_never_drifts(self):
        # Issue-anchored: every scheduled coupon lands on the issue-date
        # anniversary day (the 14th) for the bond's whole life. BB publishes
        # next_coupon_date ALREADY rolled to a working day — anchoring on it
        # would corrupt the schedule; this guards against that regression.
        events = generate_coupon_schedule(
            _bond_row(next_coupon_date=date(2026, 12, 15)))  # BB's rolled date
        assert events, "expected a schedule"
        assert all(e["scheduled_date"].day == 14 for e in events), \
            [str(e["scheduled_date"]) for e in events if e["scheduled_date"].day != 14]

    def test_final_coupon_on_maturity(self):
        events = generate_coupon_schedule(_bond_row())
        assert events[-1]["scheduled_date"] == date(2028, 6, 14)

    def test_hfly_count_matches_life(self):
        # 5-year HFLY bond → 10 coupons
        events = generate_coupon_schedule(_bond_row())
        assert len(events) == 10

    def test_standard_amounts_use_approx_formula(self):
        events = generate_coupon_schedule(_bond_row())
        for e in events:
            if e.get("is_short_coupon"):
                continue
            assert abs(e["amount_bdt_mill"] - 10000.0 * 0.114 / 2) < 0.01

    def test_real_bond_bd0926231152_schedule(self):
        # 15Y BGTB 21/12/2026: issue 2011-12-21, HFLY → coupons on the 21st,
        # final coupon on maturity 2026-12-21, 21-Jun-2026 in schedule.
        events = generate_coupon_schedule(_bond_row(
            isin="BD0926231152",
            issue_date=date(2011, 12, 21),
            maturity_date=date(2026, 12, 21),
            coupon_rate_pct=11.00,
            outstanding_bdt_mill=2000.00,
            last_coupon_date=date(2025, 12, 21),
            next_coupon_date=date(2026, 6, 21),
        ))
        scheduled = sorted(e["scheduled_date"] for e in events)
        assert scheduled[-1] == date(2026, 12, 21)
        assert date(2026, 6, 21) in scheduled
