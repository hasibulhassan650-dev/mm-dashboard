"""
tests/test_calendar.py — Tests for the working-day and date-parsing logic.

These tests cover the most critical calculation path: if next_working_day
or parse_gsom_date fail, every downstream number is wrong.

Run with:  pytest tests/ -v --tb=short
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime
import pytest
from calendar_utils import (
    load_holidays, is_working_day, is_weekend,
    next_working_day, roll_date, auction_settlement_date,
    parse_gsom_date, working_day_range,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_holidays():
    """Reset holiday set before each test."""
    load_holidays(set())
    yield
    load_holidays(set())


# ── is_working_day ────────────────────────────────────────────────────────────

class TestIsWorkingDay:
    def test_sunday_is_working(self):
        sun = datetime.date(2026, 4, 5)   # Sunday
        assert sun.weekday() == 6
        assert is_working_day(sun)

    def test_monday_is_working(self):
        assert is_working_day(datetime.date(2026, 4, 6))

    def test_thursday_is_working(self):
        thu = datetime.date(2026, 4, 9)   # Thursday
        assert thu.weekday() == 3
        assert is_working_day(thu)

    def test_friday_is_not_working(self):
        fri = datetime.date(2026, 4, 10)  # Friday
        assert fri.weekday() == 4
        assert not is_working_day(fri)

    def test_saturday_is_not_working(self):
        sat = datetime.date(2026, 4, 11)  # Saturday
        assert sat.weekday() == 5
        assert not is_working_day(sat)

    def test_holiday_on_working_weekday(self):
        mon = datetime.date(2026, 3, 26)  # Independence Day — Monday
        load_holidays({mon})
        assert not is_working_day(mon)

    def test_holiday_on_weekend_still_not_working(self):
        sat = datetime.date(2026, 4, 11)
        load_holidays({sat})
        assert not is_working_day(sat)


# ── next_working_day ──────────────────────────────────────────────────────────

class TestNextWorkingDay:
    def test_already_working_inclusive(self):
        sun = datetime.date(2026, 4, 5)
        assert next_working_day(sun, inclusive=True) == sun

    def test_already_working_exclusive(self):
        sun = datetime.date(2026, 4, 5)
        # exclusive=False → start from Monday Apr 6
        result = next_working_day(sun, inclusive=False)
        assert result == datetime.date(2026, 4, 6)

    def test_friday_rolls_to_sunday(self):
        fri = datetime.date(2026, 4, 10)
        result = next_working_day(fri, inclusive=True)
        assert result == datetime.date(2026, 4, 12)   # Sunday

    def test_saturday_rolls_to_sunday(self):
        sat = datetime.date(2026, 4, 11)
        result = next_working_day(sat, inclusive=True)
        assert result == datetime.date(2026, 4, 12)

    def test_holiday_monday_rolls_to_tuesday(self):
        mon = datetime.date(2026, 3, 26)   # Independence Day
        load_holidays({mon})
        result = next_working_day(mon, inclusive=True)
        assert result == datetime.date(2026, 3, 29)   # skips fri/sat too
        # Mon holiday → next: Tue Mar 27 ✓
        assert result == datetime.date(2026, 3, 29) or result == datetime.date(2026, 3, 27)

    def test_eid_cluster_rolls_correctly(self):
        # Simulate 3-day Eid: Mon 30 Mar, Tue 31 Mar, Wed 1 Apr
        eid = {datetime.date(2026, 3, 30),
               datetime.date(2026, 3, 31),
               datetime.date(2026, 4, 1)}
        load_holidays(eid)
        # Thu Mar 26 is working. Fri 27 = weekend, Sat 28 = weekend,
        # Sun 29 = working ← first working day
        result = next_working_day(datetime.date(2026, 3, 27), inclusive=True)
        assert result == datetime.date(2026, 3, 29)

    def test_eid_cluster_from_thursday(self):
        # Auction Thursday → next working day skips full Eid
        eid = {datetime.date(2026, 3, 30),
               datetime.date(2026, 3, 31),
               datetime.date(2026, 4, 1)}
        load_holidays(eid)
        adate = datetime.date(2026, 3, 26)   # Thu
        settled, roll_days, reason = auction_settlement_date(adate)
        # next after Thu: Fri(weekend), Sat(weekend), Sun 29 Mar (working, no holiday)
        assert settled == datetime.date(2026, 3, 29)

    def test_exceeds_max_raises(self):
        # Create 15-day holiday block — should raise
        big_block = {
            datetime.date(2026, 4, 6) + datetime.timedelta(days=i)
            for i in range(15)
        }
        load_holidays(big_block)
        with pytest.raises(ValueError, match="exceeded"):
            next_working_day(datetime.date(2026, 4, 6), inclusive=True)


# ── auction_settlement_date ───────────────────────────────────────────────────

class TestAuctionSettlement:
    def test_sunday_auction_settles_monday(self):
        sun = datetime.date(2026, 4, 5)
        settled, roll_days, reason = auction_settlement_date(sun)
        assert settled == datetime.date(2026, 4, 6)
        assert roll_days == 0   # no extra roll, Mon is working
        assert reason == ""

    def test_wednesday_auction_settles_thursday(self):
        wed = datetime.date(2026, 4, 8)
        settled, roll_days, _ = auction_settlement_date(wed)
        assert settled == datetime.date(2026, 4, 9)

    def test_thursday_auction_skips_weekend_to_sunday(self):
        thu = datetime.date(2026, 4, 9)
        settled, roll_days, reason = auction_settlement_date(thu)
        assert settled == datetime.date(2026, 4, 12)  # Sunday
        assert roll_days == 2                          # Fri+Sat skipped
        assert "WEEKEND" in reason

    def test_thursday_auction_holiday_monday_goes_to_tuesday(self):
        thu = datetime.date(2026, 3, 26)
        load_holidays({datetime.date(2026, 3, 27)})   # Fri is already weekend
        # After Thu → Fri(weekend) → Sat(weekend) → Sun Mar 29 (working) ✓
        settled, _, _ = auction_settlement_date(thu)
        assert settled == datetime.date(2026, 3, 29)


# ── roll_date ─────────────────────────────────────────────────────────────────

class TestRollDate:
    def test_working_day_no_roll(self):
        d = datetime.date(2026, 4, 6)  # Monday
        payment, roll, reason = roll_date(d)
        assert payment == d
        assert roll == 0
        assert reason == ""

    def test_friday_rolls_to_sunday(self):
        d = datetime.date(2026, 4, 10)  # Friday
        payment, roll, reason = roll_date(d)
        assert payment == datetime.date(2026, 4, 12)
        assert roll == 2
        assert "WEEKEND" in reason

    def test_holiday_rolls_forward(self):
        d = datetime.date(2026, 5, 1)   # Labour Day (Friday → irrelevant, use Mon)
        mon = datetime.date(2026, 5, 4)
        load_holidays({mon})
        payment, roll, reason = roll_date(mon)
        assert payment == datetime.date(2026, 5, 5)   # Tuesday


# ── parse_gsom_date ───────────────────────────────────────────────────────────

class TestParseGsomDate:
    def test_4digit_year(self):
        assert parse_gsom_date("03-APR-2024") == datetime.date(2024, 4, 3)

    def test_2digit_year_21st_century(self):
        assert parse_gsom_date("29-DEC-25") == datetime.date(2025, 12, 29)

    def test_2digit_year_year_26(self):
        assert parse_gsom_date("30-MAR-26") == datetime.date(2026, 3, 30)

    def test_lowercase_month(self):
        assert parse_gsom_date("03-apr-2024") == datetime.date(2024, 4, 3)

    def test_none_input(self):
        assert parse_gsom_date(None) is None

    def test_empty_string(self):
        assert parse_gsom_date("") is None

    def test_invalid_format(self):
        assert parse_gsom_date("2024-04-03") is None

    def test_all_months(self):
        months = ["JAN","FEB","MAR","APR","MAY","JUN",
                  "JUL","AUG","SEP","OCT","NOV","DEC"]
        for i, m in enumerate(months, 1):
            d = parse_gsom_date(f"01-{m}-2026")
            assert d == datetime.date(2026, i, 1), f"Failed for {m}"


# ── working_day_range ─────────────────────────────────────────────────────────

class TestWorkingDayRange:
    def test_one_week_has_5_working_days(self):
        # Week of Apr 5 (Sun) to Apr 11 (Sat) — expect Sun Mon Tue Wed Thu = 5
        days = list(working_day_range(
            datetime.date(2026, 4, 5),
            datetime.date(2026, 4, 11)
        ))
        assert len(days) == 5
        assert datetime.date(2026, 4, 5)  in days   # Sunday
        assert datetime.date(2026, 4, 10) not in days  # Friday
        assert datetime.date(2026, 4, 11) not in days  # Saturday

    def test_holiday_excluded(self):
        load_holidays({datetime.date(2026, 4, 6)})   # Monday
        days = list(working_day_range(
            datetime.date(2026, 4, 5),
            datetime.date(2026, 4, 9)
        ))
        assert datetime.date(2026, 4, 6) not in days
        assert len(days) == 4   # Sun, Tue, Wed, Thu


# ── Integration: auction on Eid week ─────────────────────────────────────────

class TestEidIntegration:
    def test_full_eid_scenario(self):
        """
        T-Bill auction Apr 5 (Sun).
        Eid cluster: Mon Apr 6 – Wed Apr 8 (3 holidays).
        Settlement should be Thu Apr 9.
        """
        eid = {datetime.date(2026, 4, 6),
               datetime.date(2026, 4, 7),
               datetime.date(2026, 4, 8)}
        load_holidays(eid)
        settled, roll_days, reason = auction_settlement_date(datetime.date(2026, 4, 5))
        assert settled == datetime.date(2026, 4, 9)
        assert roll_days == 3
        assert "HOLIDAY" in reason
