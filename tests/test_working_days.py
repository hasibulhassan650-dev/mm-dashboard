"""
tests/test_working_days.py

Tests for the next-working-day algorithm.
These are the most critical tests: wrong dates corrupt every cash flow amount.

Run with: pytest tests/test_working_days.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import date

from engine.working_days import (
    is_working_day,
    next_working_day,
    roll_info,
    roll_info_from_exclusive,
    DataError,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def no_holidays():
    return set()


@pytest.fixture
def eid_holidays():
    """Simulate a 3-day Eid cluster (Mon–Wed)."""
    return {
        date(2026, 3, 30),  # Monday
        date(2026, 3, 31),  # Tuesday
        date(2026, 4, 1),   # Wednesday
    }


@pytest.fixture
def independence_day():
    return {date(2026, 3, 26)}  # Thursday


# ── is_working_day ─────────────────────────────────────────────────────────────

class TestIsWorkingDay:
    def test_sunday_is_working(self, no_holidays):
        d = date(2026, 4, 5)   # Sunday
        assert is_working_day(d, no_holidays) is True

    def test_monday_is_working(self, no_holidays):
        d = date(2026, 4, 6)   # Monday
        assert is_working_day(d, no_holidays) is True

    def test_thursday_is_working(self, no_holidays):
        d = date(2026, 4, 9)   # Thursday
        assert is_working_day(d, no_holidays) is True

    def test_friday_is_not_working(self, no_holidays):
        d = date(2026, 4, 10)  # Friday
        assert is_working_day(d, no_holidays) is False

    def test_saturday_is_not_working(self, no_holidays):
        d = date(2026, 4, 11)  # Saturday
        assert is_working_day(d, no_holidays) is False

    def test_holiday_on_working_weekday_is_not_working(self, independence_day):
        d = date(2026, 3, 26)  # Thursday but holiday
        assert is_working_day(d, independence_day) is False

    def test_holiday_on_friday_still_not_working(self):
        friday_holiday = {date(2026, 4, 10)}
        assert is_working_day(date(2026, 4, 10), friday_holiday) is False


# ── next_working_day (inclusive) ──────────────────────────────────────────────

class TestNextWorkingDayInclusive:
    def test_sunday_inclusive_returns_sunday(self, no_holidays):
        d = date(2026, 4, 5)  # Sunday
        assert next_working_day(d, no_holidays, inclusive=True) == d

    def test_friday_rolls_to_sunday(self, no_holidays):
        d = date(2026, 4, 10)  # Friday
        expected = date(2026, 4, 12)  # Sunday
        assert next_working_day(d, no_holidays, inclusive=True) == expected

    def test_saturday_rolls_to_sunday(self, no_holidays):
        d = date(2026, 4, 11)  # Saturday
        expected = date(2026, 4, 12)  # Sunday
        assert next_working_day(d, no_holidays, inclusive=True) == expected

    def test_thursday_plus_holiday_rolls_to_next_sunday(self, independence_day):
        d = date(2026, 3, 26)   # Thursday but Independence Day
        # Fri 27 = weekend, Sat 28 = weekend, Sun 29 = working
        expected = date(2026, 3, 29)
        assert next_working_day(d, independence_day, inclusive=True) == expected

    def test_eid_cluster_rolls_past_cluster(self, eid_holidays):
        # Mon 30-Mar, Tue 31-Mar, Wed 1-Apr are Eid holidays
        d = date(2026, 3, 30)  # Monday — Eid
        # Thu 2-Apr = working day
        expected = date(2026, 4, 2)
        assert next_working_day(d, eid_holidays, inclusive=True) == expected

    def test_already_on_working_day_no_roll(self, no_holidays):
        d = date(2026, 4, 7)  # Tuesday
        assert next_working_day(d, no_holidays, inclusive=True) == d

    def test_wednesday_no_holiday_no_roll(self, no_holidays):
        d = date(2026, 4, 8)  # Wednesday
        assert next_working_day(d, no_holidays, inclusive=True) == d


# ── next_working_day (exclusive) — auction settlement ─────────────────────────

class TestNextWorkingDayExclusive:
    def test_sunday_auction_settles_monday(self, no_holidays):
        auction = date(2026, 4, 5)   # Sunday
        info = roll_info_from_exclusive(auction, no_holidays)
        assert info["payment_date"] == date(2026, 4, 6)  # Monday

    def test_thursday_auction_before_eid_settles_after_eid(self, eid_holidays):
        # Auction Thursday 26-Mar, Eid Mon-Wed 30-31 Mar, 1 Apr
        # Next day after Thu = Fri 27 (weekend) → Sat 28 (weekend)
        # → Sun 29 = working ✓
        auction = date(2026, 3, 26)
        info = roll_info_from_exclusive(auction, eid_holidays)
        assert info["payment_date"] == date(2026, 3, 29)

    def test_wednesday_auction_settles_thursday(self, no_holidays):
        auction = date(2026, 4, 8)  # Wednesday
        info = roll_info_from_exclusive(auction, no_holidays)
        assert info["payment_date"] == date(2026, 4, 9)  # Thursday

    def test_thursday_auction_settles_next_sunday(self, no_holidays):
        # Thursday + 1 = Friday (weekend) → Saturday (weekend) → Sunday ✓
        auction = date(2026, 4, 9)   # Thursday
        info = roll_info_from_exclusive(auction, no_holidays)
        assert info["payment_date"] == date(2026, 4, 12)  # Sunday

    def test_roll_reason_populated_when_rolled(self, independence_day):
        # Wednesday 25-Mar auction, next day is Thu 26 = Independence Day
        # then Fri 27 = Fri weekend, Sat 28 = Sat, Sun 29 = working
        auction = date(2026, 3, 25)  # Wednesday
        info = roll_info_from_exclusive(auction, independence_day)
        assert info["payment_date"] == date(2026, 3, 29)
        assert "Holiday" in info["roll_reason"]
        assert info["days_rolled"] == 4

    def test_days_rolled_is_correct(self, no_holidays):
        # Thursday auction → settle Sunday = 3 calendar days
        auction = date(2026, 4, 9)
        info = roll_info_from_exclusive(auction, no_holidays)
        assert info["days_rolled"] == 3


# ── roll_info ──────────────────────────────────────────────────────────────────

class TestRollInfo:
    def test_no_roll_on_working_day(self, no_holidays):
        d = date(2026, 4, 5)  # Sunday
        info = roll_info(d, no_holidays)
        assert info["scheduled_date"] == d
        assert info["payment_date"] == d
        assert info["days_rolled"] == 0
        assert info["was_rolled"] is False
        assert info["roll_reason"] == ""

    def test_friday_roll_populates_reason(self, no_holidays):
        d = date(2026, 4, 10)  # Friday
        info = roll_info(d, no_holidays)
        assert info["days_rolled"] == 2
        assert info["was_rolled"] is True
        assert "Fri" in info["roll_reason"]

    def test_holiday_roll_populates_reason(self, independence_day):
        d = date(2026, 3, 26)  # Thursday — Independence Day
        info = roll_info(d, independence_day)
        assert info["was_rolled"] is True
        assert "Holiday" in info["roll_reason"]


# ── DataError ─────────────────────────────────────────────────────────────────

class TestDataError:
    def test_excessive_holiday_cluster_raises(self):
        """If holiday set covers 15 consecutive days, DataError must be raised."""
        start = date(2026, 4, 5)
        from datetime import timedelta
        huge_holidays = {start + timedelta(days=i) for i in range(15)}
        with pytest.raises(DataError):
            next_working_day(start, huge_holidays, inclusive=True)
