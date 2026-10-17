"""BB's results page carries more than we used to keep.

Three columns were being discarded, each of which matters:

  * ISIN, which also carries the "(Re-issuance: N Yr.)" marker — the ONLY place
    a re-issue is flagged, and the reason one ISIN legitimately appears at
    several issue dates with different cut-offs;
  * the accepted SALE value, which is the cash the market actually paid and on
    a premium re-issue differs materially from the accepted FACE value
    (02-Sep-2026 20Y: face 3,500.00, sale 3,689.28);
  * bid counts, yield ranges, weighted-average price and the devolvement yield.

The HTML below is BB's live Sep-2026 table, column for column.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetchers.treasury import _parse_isin_cell, _parse_page

SNAP = datetime.date(2026, 9, 28)

# Header rows exactly as BB prints them, then three real data rows: a re-issued
# 20Y bond sold at a premium, a re-issued FRTB, and a plain T-Bill at a discount.
_FULL = """
<table>
 <tr><th>Issue date *</th><th>ISIN Number</th><th>Remaining Maturity (aprx)</th>
     <th>Tenor and name</th><th colspan="3">Bids received</th><th colspan="7">Bids accepted</th></tr>
 <tr><th>No of bids</th><th>Face value (Cr.Tk.)</th><th>Range of yields (%)</th>
     <th>No of bids</th><th>Face value (Cr.Tk.)</th><th>Sale value (Cr.Tk.)</th>
     <th>Range of yields (%)</th><th>Weighted average Price (taka)</th>
     <th>Cut off yield (%)</th><th>Standard/ Devolvement Yield (%)</th></tr>
 <tr><td>02/09/2026</td><td>BD0929441204 (Re-issuance: 2.73 Yr.)</td><td>2yr</td>
     <td>20yr T.Bond</td><td>339</td><td>9773.16</td><td>8.7280-11.7100</td>
     <td>257</td><td>3500.00</td><td>3689.28</td><td>8.7280-8.8685</td>
     <td>105.4081</td><td>8.8685</td><td>8.75</td></tr>
 <tr><td>02/09/2026</td><td>BD0929261032 (Re-issuance:  2.35 Yr.)</td><td>3yr</td>
     <td>3yr FRT.Bond</td><td>44</td><td>1109.37</td><td>8.8899-11.8600</td>
     <td>21</td><td>500.00</td><td>511.79</td><td>8.8899-9.6948</td>
     <td>102.3581</td><td>9.6948</td><td>9.73</td></tr>
 <tr><td>07/09/2026</td><td>BD0909110266</td><td>91 days</td><td>91 days T.Bill</td>
     <td>391</td><td>8900.11</td><td>8.4795-10.5899</td><td>315</td><td>3000.00</td>
     <td>2937.48</td><td>8.4795-8.5899</td><td>97.9160</td><td>8.5899</td><td></td></tr>
</table>
"""

# An older, narrower BB layout: no sale value, no bid counts.
_COMPACT = """
<table>
 <tr><th>Issue date</th><th>ISIN</th><th>Rem</th><th>Tenor</th><th>Offered</th>
     <th>Accepted</th><th>Price</th><th>Cut off</th><th>Std</th></tr>
 <tr><td>04/03/2009</td><td>BD0911110123</td><td>91 days</td><td>91 days T.Bill</td>
     <td>256.25</td><td>26.00</td><td>98.1000</td><td>8.16</td><td></td></tr>
</table>
"""


def _rows(html):
    return _parse_page(html, SNAP)


def _by_tenor(rows, t):
    """Lookup by the ORIGINAL tenor. BB labels a re-issue by its REMAINING
    maturity, so the 20-year bond with 2.73 years left is tenor_label '2Y' --
    right for the curve, wrong for identifying the instrument."""
    return next(r for r in rows if (r["original_tenor_label"] or r["tenor_label"]) == t)


class TestIsinCell:
    def test_a_plain_isin(self):
        assert _parse_isin_cell("BD0909110266") == ("BD0909110266", False, None)

    def test_a_reissue_carries_its_marker(self):
        isin, re_, rem = _parse_isin_cell("BD0929441204 (Re-issuance: 2.73 Yr.)")
        assert isin == "BD0929441204"
        assert re_ is True
        assert rem.startswith("2.73")

    def test_extra_whitespace_does_not_break_it(self):
        isin, re_, _ = _parse_isin_cell("BD0929261032 (Re-issuance:  2.35 Yr.)")
        assert isin == "BD0929261032" and re_ is True

    def test_an_empty_cell_is_not_an_error(self):
        assert _parse_isin_cell("") == (None, False, None)
        assert _parse_isin_cell(None) == (None, False, None)


class TestTheFullLayout:
    def test_every_auction_is_parsed(self):
        assert len(_rows(_FULL)) == 3

    def test_isin_and_reissue_are_kept(self):
        r = _by_tenor(_rows(_FULL), "20Y")
        assert r["isin"] == "BD0929441204"
        assert r["is_reissue"] is True
        assert r["reissue_remaining"].startswith("2.73")

    def test_a_non_reissue_is_not_flagged(self):
        r = _by_tenor(_rows(_FULL), "91D")
        assert r["isin"] == "BD0909110266"
        assert r["is_reissue"] is False
        assert r["reissue_remaining"] is None

    def test_face_value_and_sale_value_are_different_fields(self):
        # The whole point. Merging them would turn a premium into a par sale.
        r = _by_tenor(_rows(_FULL), "20Y")
        assert r["accepted_bdt_crore"] == 3500.00
        assert r["sale_value_bdt_crore"] == 3689.28
        assert r["accepted_bdt_crore"] != r["sale_value_bdt_crore"]

    def test_a_bill_sells_at_a_discount_and_that_survives_too(self):
        r = _by_tenor(_rows(_FULL), "91D")
        assert r["accepted_bdt_crore"] == 3000.00
        assert r["sale_value_bdt_crore"] == 2937.48   # discount instrument

    def test_bids_received_is_not_confused_with_accepted(self):
        r = _by_tenor(_rows(_FULL), "20Y")
        assert r["offered_bdt_crore"] == 9773.16      # bids received
        assert r["bids_received_count"] == 339
        assert r["bids_accepted_count"] == 257

    def test_yield_ranges_are_kept_verbatim(self):
        # Two numbers; collapsing them to one would invent a figure.
        r = _by_tenor(_rows(_FULL), "20Y")
        assert r["yield_range_received"] == "8.7280-11.7100"
        assert r["yield_range_accepted"] == "8.7280-8.8685"

    def test_price_cutoff_and_devolvement_yield(self):
        r = _by_tenor(_rows(_FULL), "20Y")
        assert r["weighted_avg_price"] == 105.4081
        assert r["cutoff_yield_pct"] == 8.8685
        assert r["devolvement_yield_pct"] == 8.75

    def test_a_blank_devolvement_yield_is_none_not_zero(self):
        # "not published" and "zero per cent" are different claims.
        r = _by_tenor(_rows(_FULL), "91D")
        assert r["devolvement_yield_pct"] is None

    def test_a_reissue_keeps_both_its_remaining_and_original_tenor(self):
        # Two different facts: 2.73 years left on a bond that was issued for 20.
        # The sheet shows the original; the yield curve uses the remaining.
        r = _by_tenor(_rows(_FULL), "20Y")
        assert r["tenor_label"] == "2Y"            # remaining maturity
        assert r["original_tenor_label"] == "20Y"  # what the security is
        assert r["original_tenor_years"] == 20.0

    def test_the_frtb_keeps_its_own_label(self):
        rows = _rows(_FULL)
        frtb = next(r for r in rows if r["security_type"] == "FRTB")
        assert frtb["tenor_label"].endswith("_FRTB")
        assert frtb["isin"] == "BD0929261032" and frtb["is_reissue"] is True


class TestOlderNarrowerLayouts:
    """BB's historical pages genuinely have fewer columns. A missing column must
    come out as None — a 0 would read as 'no bids' or 'sold at par'."""

    def test_the_compact_layout_still_parses(self):
        rows = _rows(_COMPACT)
        assert len(rows) == 1
        assert rows[0]["cutoff_yield_pct"] == 8.16

    def test_isin_is_still_captured_when_present(self):
        assert _rows(_COMPACT)[0]["isin"] == "BD0911110123"

    @pytest.mark.parametrize("field", [
        "sale_value_bdt_crore", "bids_received_count", "bids_accepted_count",
        "yield_range_received", "yield_range_accepted",
    ])
    def test_absent_columns_are_none_never_zero(self, field):
        assert _rows(_COMPACT)[0][field] is None


class TestOneIsinTwoAuctions:
    """A re-issue is the same ISIN sold again at a different date and price. The
    rows must stay separate; keying on ISIN would collapse them."""

    def test_two_auctions_of_one_isin_stay_two_rows(self):
        html = _FULL.replace(
            "<td>07/09/2026</td><td>BD0909110266</td><td>91 days</td><td>91 days T.Bill</td>",
            "<td>07/09/2026</td><td>BD0929441204 (Re-issuance: 2.70 Yr.)</td>"
            "<td>2yr</td><td>20yr T.Bond</td>")
        rows = [r for r in _rows(html) if r["isin"] == "BD0929441204"]
        assert len(rows) == 2
        assert {str(r["issue_date"]) for r in rows} == {"2026-09-02", "2026-09-07"}
        # different auctions -> different cut-offs, both kept
        assert len({r["cutoff_yield_pct"] for r in rows}) == 2


class TestOneAuctionCannotBeStoredUnderTwoLabels:
    """The 10-Oct-2026 duplicate, and why it was not a storage problem.

    ISIN BD0928101056, auctioned 03-Feb-2026: a 5-year bond re-issued with
    2.60 years left. It ended up in the database TWICE — once correctly as
    `2Y` (BB labels a re-issue by its REMAINING maturity, which is the point
    it occupies on the curve) and once as `5Y`, with the same cut-off, the
    same 2,500 crore and the same sale value. primary_yield_snapshots is keyed
    (tenor_label, auction_date), so the wrong label did not overwrite the
    right one, it landed beside it — and confirm_auctions_from_results then
    gave each row its own auction_events row, double-counting 2,500 crore of
    outflow on 05-Feb.

    The cause was a silent fallback: when BB's remaining-maturity column could
    not be identified, the parser read "Tenor and name" instead, which holds
    the ORIGINAL tenor. Same code, same page, a label that means something
    different. Two defences now:

      * that column is REQUIRED — a layout without it stops the parse and says
        so, rather than relabelling every row;
      * BB states the remaining maturity twice on a re-issue (its own column,
        and inside the ISIN cell as "Re-issuance: 2.60 Yr."), so the two are
        compared and the explicit marker wins.
    """

    _REISSUE = """
    <table>
     <tr><th>Issue date *</th><th>ISIN Number</th><th>Remaining Maturity (aprx)</th>
         <th>Tenor and name</th><th colspan="3">Bids received</th>
         <th colspan="7">Bids accepted</th></tr>
     <tr><th>No of bids</th><th>Face value (Cr.Tk.)</th><th>Range of yields (%)</th>
         <th>No of bids</th><th>Face value (Cr.Tk.)</th><th>Sale value (Cr.Tk.)</th>
         <th>Range of yields (%)</th><th>Weighted average Price (taka)</th>
         <th>Cut off yield (%)</th><th>Standard/ Devolvement Yield (%)</th></tr>
     <tr><td>05/02/2026</td><td>BD0928101056 (Re-issuance: 2.60 Yr.)</td><td>REM</td>
         <td>5yr T.Bond</td><td>120</td><td>5000.00</td><td>9.8000-11.0000</td>
         <td>60</td><td>2500.00</td><td>2425.59</td><td>10.1000-10.4598</td>
         <td>97.0236</td><td>10.4598</td><td>10.50</td></tr>
    </table>
    """

    def test_the_label_is_the_remaining_maturity_not_the_original_tenor(self):
        rows = _parse_page(self._REISSUE.replace("REM", "2yr"), SNAP)
        assert len(rows) == 1
        assert rows[0]["tenor_label"] == "2Y"
        assert rows[0]["original_tenor_label"] == "5Y"

    def test_bbs_grid_classification_is_what_the_label_keeps(self):
        """The column and the marker are two different facts, not one twice.

        The column is BB filing the bond on its standard tenor grid — 2 / 5 /
        10 / 15 / 20 — and that is what every chart, export and curve keys on.
        The marker is the exact remaining life. Preferring the marker produced
        labels BB does not auction: on 16-Oct-2026 a 20-year bond with 16.26
        years left became "16Y" and another with 18.93 became "18Y", tenors no
        chart plots and which the yield-label check reported as unknown within
        a day. BB was not contradicting itself; it was stating two true things.
        """
        # The real pair from 22-Sep-2026: a 20-year bond with 16.26 years left,
        # which BB files under its 15Y point.
        html = (self._REISSUE.replace("REM", "15yr")
                .replace("2.60 Yr", "16.26 Yr").replace("5yr T.Bond", "20yr T.Bond"))
        rows = _parse_page(html, SNAP)
        assert rows[0]["tenor_label"] == "15Y",             "BB's own grid classification must survive"
        # The exact life is not lost — it is kept where it belongs.
        assert rows[0]["reissue_remaining"] == "16.26 Yr"
        assert rows[0]["original_tenor_label"] == "20Y"

    def test_the_difference_is_recorded_without_being_acted_on(self):
        """A bond filed a year or two from its exact life is BB rounding onto
        its own grid. It is counted, so the fingerprint shows how often that
        happens, and nothing is changed. It is NOT a skip: no row was dropped.
        """
        meta = {}
        rows = _parse_page(self._REISSUE.replace("REM", "5yr"), SNAP, meta)
        assert len(rows) == 1 and rows[0]["tenor_label"] == "5Y"
        assert meta["rows_parsed"] == 1
        assert meta["rows_skipped"] == 0, "nothing was dropped"
        assert sum(meta["corrections"].values()) == 1
        assert "actually remaining" in " ".join(meta["corrections"])

    def test_an_implausible_distance_means_one_reading_is_wrong(self):
        """Grid spacing explains two or three years, not thirteen. Beyond that
        one of the two is misread, and the row is skipped rather than guessed.
        """
        meta = {}
        rows = _parse_page(self._REISSUE.replace("REM", "20yr"), SNAP, meta)
        assert rows == [], "20Y against a 2.60 Yr marker was stored anyway"
        assert sum(meta["skip_reasons"].values()) == 1
        assert "misread" in " ".join(meta["skip_reasons"])


    def test_a_layout_with_no_remaining_maturity_column_refuses(self):
        """Rather than relabelling every row from the tenor-name column."""
        import pytest as _pt
        from fetchers import source_watch as SW
        gone = self._REISSUE.replace("<th>Remaining Maturity (aprx)</th>",
                                     "<th>Period left</th>").replace("REM", "2yr")
        with _pt.raises(SW.StructureChanged) as exc:
            _parse_page(gone, SNAP)
        assert "rem" in str(exc.value)

    def test_a_fractional_year_parses_as_its_floor(self):
        """2.60 years left sits on the 2Y point until it has less than two
        years to run; rounding up would place it on a point it never reaches."""
        from fetchers.treasury import _rem_to_tenor
        assert _rem_to_tenor("2.60 Yr") == ("2Y", "T_BOND")
        assert _rem_to_tenor("2.60 Yr.") == ("2Y", "T_BOND")
        assert _rem_to_tenor("19.75 Yr") == ("19Y", "T_BOND")
