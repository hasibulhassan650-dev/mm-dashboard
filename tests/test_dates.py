"""tests/test_dates.py — date parsing and fiscal year tests."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import date
from parser.dates import parse_gsom_date, fiscal_year_for


class TestParseGsomDate:
    def test_four_digit_year_bond(self):
        d, raw = parse_gsom_date("03-APR-2024")
        assert d == date(2024, 4, 3)
        assert raw == "03-APR-2024"

    def test_two_digit_year_tbill(self):
        d, _ = parse_gsom_date("29-DEC-25")
        assert d == date(2025, 12, 29)

    def test_two_digit_year_2026(self):
        d, _ = parse_gsom_date("30-MAR-26")
        assert d == date(2026, 3, 30)

    def test_lowercase_month(self):
        d, _ = parse_gsom_date("05-jun-2026")
        assert d == date(2026, 6, 5)

    def test_empty_string_returns_none(self):
        d, raw = parse_gsom_date("")
        assert d is None

    def test_invalid_format_returns_none(self):
        d, _ = parse_gsom_date("2026-04-03")  # ISO format — not GSOM
        assert d is None

    def test_invalid_month_returns_none(self):
        d, _ = parse_gsom_date("03-XYZ-2024")
        assert d is None

    def test_all_months_parse(self):
        months = ["JAN","FEB","MAR","APR","MAY","JUN",
                  "JUL","AUG","SEP","OCT","NOV","DEC"]
        for i, m in enumerate(months, 1):
            d, _ = parse_gsom_date(f"15-{m}-2025")
            assert d == date(2025, i, 15), f"Failed for month {m}"


class TestFiscalYear:
    def test_april_is_FY2025_26(self):
        assert fiscal_year_for(date(2026, 4, 9)) == "2025-26"

    def test_july_starts_new_FY(self):
        assert fiscal_year_for(date(2025, 7, 1)) == "2025-26"

    def test_june_ends_prev_FY(self):
        assert fiscal_year_for(date(2025, 6, 30)) == "2024-25"

    def test_january_mid_FY(self):
        assert fiscal_year_for(date(2026, 1, 8)) == "2025-26"
