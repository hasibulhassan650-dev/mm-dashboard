"""
tests/test_coupon.py

Tests for coupon event generation, formula correctness, and schedule derivation.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import date

from engine.coupon import generate_coupon_schedule, _approx_coupon, _act365_coupon
from config import FREQ_HFLY, FREQ_QRTY, FREQ_NONE


NO_HOLIDAYS = set()


# ── Formula correctness ────────────────────────────────────────────────────────

class TestCouponFormulas:
    def test_hfly_approx_example_from_blueprint(self):
        # Blueprint example: 2000mn × 11%/2 = 110mn
        result = _approx_coupon(2000.00, 11.00, FREQ_HFLY)
        assert result == 110.00

    def test_qrty_approx(self):
        # 5000mn × 11.72% / 4
        result = _approx_coupon(5000.00, 11.72, FREQ_QRTY)
        assert result == pytest.approx(146.50, rel=1e-4)

    def test_act365_hfly_example_from_blueprint(self):
        # Blueprint: 2000 × 11% × 182/365 = 109.70
        result = _act365_coupon(2000.00, 11.00, 182)
        assert result == pytest.approx(109.70, rel=1e-3)

    def test_act365_vs_approx_diff_under_1pct(self):
        # The difference must be < 1% for any standard HFLY bond
        approx = _approx_coupon(2000.00, 11.00, FREQ_HFLY)
        exact  = _act365_coupon(2000.00, 11.00, 182)
        diff_pct = abs(approx - exact) / approx * 100
        assert diff_pct < 1.0


# ── Schedule generation ────────────────────────────────────────────────────────

class TestCouponSchedule:
    def test_bd0926231152_generates_correct_schedule(self):
        """
        BD0926231152 — 15Y BGTB 21/12/2026
        issue: 2011-12-21, maturity: 2026-12-21
        last_coupon: 2025-12-21, next_coupon: 2026-06-21
        HFLY → coupons every 6 months on the 21st
        """
        events = generate_coupon_schedule(
            isin               = "BD0926231152",
            issue_date         = date(2011, 12, 21),
            maturity_date      = date(2026, 12, 21),
            coupon_rate_pct    = 11.00,
            coupon_frequency   = FREQ_HFLY,
            last_coupon_date   = date(2025, 12, 21),
            next_coupon_date   = date(2026, 6, 21),
            outstanding_bdt_mill = 2000.00,
            settlement_date    = date(2026, 4, 2),
            source_page        = "test",
            holiday_set        = NO_HOLIDAYS,
            use_act365         = False,
        )
        assert len(events) > 0
        scheduled = sorted(e["scheduled_date"] for e in events)

        # Last coupon must equal maturity date (21-Dec-2026)
        assert scheduled[-1] == date(2026, 12, 21)

        # Next coupon (21-Jun-2026) must be in the schedule
        assert date(2026, 6, 21) in scheduled

        # All events should be COUPON type
        assert all(e["event_type"] == "COUPON" for e in events)

    def test_coupon_amount_is_correct_for_hfly(self):
        events = generate_coupon_schedule(
            isin="TEST001",
            issue_date=date(2024, 1, 1),
            maturity_date=date(2026, 1, 1),
            coupon_rate_pct=11.00,
            coupon_frequency=FREQ_HFLY,
            last_coupon_date=date(2025, 7, 1),
            next_coupon_date=date(2026, 1, 1),
            outstanding_bdt_mill=2000.00,
            settlement_date=date(2026, 4, 2),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
            use_act365=False,
        )
        coupon_amounts = [e["coupon_amount_bdt_mill"] for e in events]
        # All regular coupons should be 110.00
        for amt in coupon_amounts:
            if amt is not None:
                assert amt == pytest.approx(110.00, rel=1e-4)

    def test_no_coupons_for_tbill(self):
        """T-Bills must return empty list."""
        from config import FREQ_NONE
        events = generate_coupon_schedule(
            isin="BD0909167266",
            issue_date=date(2025, 12, 29),
            maturity_date=date(2026, 3, 30),
            coupon_rate_pct=None,
            coupon_frequency=FREQ_NONE,
            last_coupon_date=None,
            next_coupon_date=None,
            outstanding_bdt_mill=30000.00,
            settlement_date=date(2026, 3, 25),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
            use_act365=False,
        )
        assert events == []

    def test_no_coupons_after_maturity(self):
        """No coupon events should have scheduled_date > maturity_date."""
        events = generate_coupon_schedule(
            isin="TEST002",
            issue_date=date(2024, 4, 9),
            maturity_date=date(2027, 4, 9),
            coupon_rate_pct=10.00,
            coupon_frequency=FREQ_HFLY,
            last_coupon_date=date(2026, 10, 9),
            next_coupon_date=date(2027, 4, 9),
            outstanding_bdt_mill=5000.00,
            settlement_date=date(2026, 4, 2),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
            use_act365=False,
        )
        maturity = date(2027, 4, 9)
        for e in events:
            assert e["scheduled_date"] <= maturity, (
                f"Coupon on {e['scheduled_date']} is after maturity {maturity}"
            )

    def test_payment_date_is_working_day(self):
        """All payment_dates must not be Friday or Saturday."""
        from config import BD_WORKING_WEEKDAYS
        events = generate_coupon_schedule(
            isin="TEST003",
            issue_date=date(2021, 6, 16),
            maturity_date=date(2026, 6, 16),
            coupon_rate_pct=3.88,
            coupon_frequency=FREQ_HFLY,
            last_coupon_date=date(2025, 12, 16),
            next_coupon_date=date(2026, 6, 16),
            outstanding_bdt_mill=45000.00,
            settlement_date=date(2026, 4, 2),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
            use_act365=False,
        )
        for e in events:
            pd = e["payment_date"]
            assert pd.weekday() in BD_WORKING_WEEKDAYS, (
                f"Payment date {pd} is a weekend (weekday={pd.weekday()})"
            )

    def test_qrty_coupon_amount(self):
        """FRTB quarterly coupon formula check."""
        events = generate_coupon_schedule(
            isin="BD0927141038",
            issue_date=date(2024, 10, 2),
            maturity_date=date(2027, 10, 2),
            coupon_rate_pct=11.72,
            coupon_frequency=FREQ_QRTY,
            last_coupon_date=date(2026, 4, 2),
            next_coupon_date=date(2026, 7, 2),
            outstanding_bdt_mill=9297.60,
            settlement_date=date(2026, 4, 9),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
            use_act365=False,
        )
        # Regular coupon = 9297.60 × 11.72% / 4
        expected = round(9297.60 * 0.1172 / 4, 2)
        for e in events:
            if not e.get("is_short_coupon"):
                assert e["coupon_amount_bdt_mill"] == pytest.approx(expected, rel=1e-4)

    def test_formula_string_present_on_all_events(self):
        events = generate_coupon_schedule(
            isin="TEST004",
            issue_date=date(2025, 1, 8),
            maturity_date=date(2027, 1, 8),
            coupon_rate_pct=12.12,
            coupon_frequency=FREQ_HFLY,
            last_coupon_date=date(2026, 1, 8),
            next_coupon_date=date(2026, 7, 8),
            outstanding_bdt_mill=45000.00,
            settlement_date=date(2026, 4, 2),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
        )
        for e in events:
            assert "formula_string" in e
            assert e["formula_string"]  # not empty

    def test_missing_outstanding_returns_empty(self):
        events = generate_coupon_schedule(
            isin="TEST_MISSING",
            issue_date=date(2024, 1, 1),
            maturity_date=date(2026, 1, 1),
            coupon_rate_pct=10.00,
            coupon_frequency=FREQ_HFLY,
            last_coupon_date=date(2025, 7, 1),
            next_coupon_date=date(2026, 1, 1),
            outstanding_bdt_mill=None,   # ← missing
            settlement_date=date(2026, 4, 2),
            source_page="test",
            holiday_set=NO_HOLIDAYS,
        )
        assert events == []
