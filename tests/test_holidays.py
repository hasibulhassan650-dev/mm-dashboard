"""Bangladesh Bank's gazetted holiday list, and why it must be read verbatim.

Until 15-Oct-2026 nothing in this project read BB's holiday page. The URL sat
in config.py unused and `holiday_calendar` held 33 rows ending 26-Aug-2026 —
the recent ones typed INFERRED_CLOSURE, meaning the whole money market had
gone silent and a holiday was guessed from that. Guessing backwards works for
the past and not at all for the future, so the system did not know that 20 and
21 October 2026 are Durga Puja: a Tuesday and a Wednesday, five days out, with
a planned 10-year bond settlement, a 1,266.75 million coupon and three OMO
maturities booked across them.

A wrong holiday is worse than a missing one — it moves real settlement dates
off real days — so every row BB prints is checked against BB's own other two
statements of the same fact before it is believed:

    Holiday                    Date                     Day                  No of Holidays
    Durga Puja (Nabami & ...)  20 & 21 October 2026     Tuesday & Wednesday  2 Days

The dates, the first weekday name and the count must agree. The HTML in these
tests is BB's live page, row for row.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetchers.holidays import parse_date_cell, parse_page

D = datetime.date


def _page(rows_html: str) -> str:
    return f"""
    <table>
      <tr><th>Holiday</th><th>Date</th><th>Day</th><th>No of Holidays</th></tr>
      {rows_html}
    </table>
    """


def _row(name, date, day, count):
    return f"<tr><td>{name}</td><td>{date}</td><td>{day}</td><td>{count}</td></tr>"


class TestBBsRealDateStrings:
    """Every format on BB's 2026 page."""

    def test_a_single_day(self):
        assert parse_date_cell("04 February 2026") == ([D(2026, 2, 4)], None)

    def test_a_comma_and_ampersand_run(self):
        dates, err = parse_date_cell("19, 20, 21, 22 & 23 March 2026")
        assert err is None
        assert dates == [D(2026, 3, d) for d in (19, 20, 21, 22, 23)]

    def test_a_two_day_holiday(self):
        assert parse_date_cell("20 & 21 October 2026") == (
            [D(2026, 10, 20), D(2026, 10, 21)], None)

    def test_a_comma_before_the_year(self):
        assert parse_date_cell("14 April, 2026") == ([D(2026, 4, 14)], None)

    def test_a_six_day_eid(self):
        dates, err = parse_date_cell("26, 27, 28, 29, 30 & 31 May 2026")
        assert err is None and len(dates) == 6

    def test_a_run_crossing_a_month_boundary(self):
        """BB prints the year once, at the end. Defaulting the first segment to
        the current year would file that half of the holiday a year out."""
        dates, err = parse_date_cell("30 & 31 March, 1 April 2026")
        assert err is None, err
        assert dates == [D(2026, 3, 30), D(2026, 3, 31), D(2026, 4, 1)]


class TestItRefusesRatherThanGuesses:
    def test_a_string_with_no_year_is_refused(self):
        dates, err = parse_date_cell("20 & 21 October")
        assert dates == [] and "year" in err

    def test_an_impossible_date_is_refused(self):
        dates, err = parse_date_cell("31 February 2026")
        assert dates == [] and "not a real date" in err

    def test_an_unparseable_cell_yields_no_partial_dates(self):
        """Half a holiday would pass BB's own count check and be believed."""
        dates, err = parse_date_cell("to be announced")
        assert dates == [] and err

    def test_a_count_that_disagrees_is_refused(self):
        out = parse_page(_page(_row("Durga Puja", "20 &amp; 21 October 2026",
                                    "Tuesday &amp; Wednesday", "3 Days")))
        assert out["rows"] == []
        assert any("BB says 3" in k for k in out["refused"]), out["refused"]

    def test_a_weekday_that_disagrees_is_refused(self):
        """A day/month swap parses cleanly and lands on the wrong weekday —
        BB's own Day column is the only thing that catches it."""
        out = parse_page(_page(_row("Victory Day", "16 December 2026",
                                    "Thursday", "1 Day")))
        assert out["rows"] == []
        assert any("Wednesday" in k for k in out["refused"]), out["refused"]

    def test_a_refusal_is_counted_not_silent(self):
        out = parse_page(_page(
            _row("Good one", "16 December 2026", "Wednesday", "1 Day")
            + _row("Bad one", "to be announced", "Monday", "1 Day")))
        assert len(out["rows"]) == 1
        assert sum(out["refused"].values()) == 1


class TestTheWholePage:
    _REAL = _page(
        _row("*Shab-e-Barat", "04 February 2026", "Wednesday", "1 Day")
        + _row("*Eid-ul-Fitr and Jumat-ul-Wida", "19, 20, 21, 22 &amp; 23 March 2026",
               "Thursday, Friday, Saturday, Sunday &amp; Monday", "5 Days")
        + _row("Durga Puja (Nabami &amp; Bijoya Dashami)", "20 &amp; 21 October 2026",
               "Tuesday &amp; Wednesday", "2 Days")
        + _row("Bank Holiday", "31 December 2026", "Thursday", "1 Day"))

    def test_every_date_becomes_its_own_row(self):
        out = parse_page(self._REAL)
        assert len(out["rows"]) == 9, [str(r["calendar_date"]) for r in out["rows"]]
        assert out["refused"] == {}

    def test_durga_puja_lands_on_the_tuesday_and_wednesday(self):
        rows = {r["calendar_date"]: r["holiday_name"] for r in parse_page(self._REAL)["rows"]}
        assert D(2026, 10, 20) in rows and D(2026, 10, 21) in rows
        assert "Durga Puja" in rows[D(2026, 10, 20)]

    def test_rows_are_typed_as_gazetted(self):
        """So they are distinguishable from closures inferred from silence, and
        from declared working weekends, which this must never touch."""
        assert {r["holiday_type"] for r in parse_page(self._REAL)["rows"]} == {"GAZETTED"}

    def test_the_fingerprint_records_what_the_page_looked_like(self):
        meta = parse_page(self._REAL)["meta"]
        assert meta["headers"][:2] == ["holiday", "date"]
        assert meta["rows_parsed"] == 9

    def test_a_renamed_date_column_is_a_loud_failure(self):
        """Not a page that parses to zero holidays and reports success."""
        import pytest
        from fetchers import source_watch as SW
        broken = self._REAL.replace("<th>Date</th>", "<th>When</th>")
        with pytest.raises(SW.StructureChanged) as exc:
            parse_page(broken)
        assert "date" in str(exc.value)
