"""tests/test_dates.py — fiscal-year boundary tests against the LIVE config.

WHY: Bangladesh's fiscal year turns over July 1. Auction calendars, budget
borrowing plans and BB publications are all FY-keyed — an off-by-one here
files data under the wrong year (the July 2026 stale-calendar incident was
an FY-rollover blind spot).
GSOM date parsing is covered in test_calendar.py.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from config import fiscal_year


class TestFiscalYear:
    def test_july_first_starts_new_fy(self):
        assert fiscal_year(date(2026, 7, 1)) == "2026-27"

    def test_june_thirtieth_is_old_fy(self):
        assert fiscal_year(date(2026, 6, 30)) == "2025-26"

    def test_mid_year(self):
        assert fiscal_year(date(2026, 1, 15)) == "2025-26"
        assert fiscal_year(date(2025, 12, 31)) == "2025-26"
