"""The desk pastes this sheet into its own workbook, so each figure has to be the one BB printed, with the sign the
desk's sheet gives it. The two days below are the desk's own screenshots (30-Sep-26 and 1-Oct-26)."""
import datetime
import io
import json
import os
import sys

from openpyxl import load_workbook

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "routers"))
from omo_sheet_logic import ACCOUNTING, build_daily_sheet, day_block  # noqa: E402


def R(i, inst, ten, off, acc, rate, mat, net, dirn, rng=None):
    return {"id": i, "instrument": inst, "tenor_days": ten, "offered_bdt_crore": off, "accepted_bdt_crore": acc,
            "rate_pct": rate, "rate_range": rng, "maturity_bdt_crore": mat, "net_bdt_crore": net, "direction": dirn}


SEP30 = [R(1, "IBLF", 7, 610.94, 610.94, 3.0, 55.14, 555.80, "INJECTION", "3.00-4.14"),
         R(2, "AR", 1, 1651.63, 1651.63, 9.5, 0.0, 1651.63, "INJECTION"),
         R(3, "SDF", 1, 6846.0, 6846.0, 7.5, 9326.92, 2480.92, "ABSORPTION")]
OCT01 = [R(1, "IBLF", 7, 957.68, 957.68, 3.0, 997.89, -40.22, "INJECTION", "3.00-5.25"),
         R(2, "AR", 1, 0.0, 0.0, 9.5, 1652.05, -1652.05, "INJECTION"),
         R(3, "SDF", 1, 6407.0, 6407.0, 7.5, 6847.41, 440.41, "ABSORPTION")]


# BB prints its totals from UNROUNDED figures, so they are not always the sum of the printed rows (a cent off in
# 30-Sep's Grand Total maturity and net, and 1-Oct's Sub-Total maturity and Grand Total net). The desk's sheet
# carries what BB printed, so that is what the export must carry.
PT30 = json.dumps({"Sub-Total": [2262.57, 2262.57, -55.14, 2207.43],
                   "Grand Total": [-4583.43, -4583.43, 9271.77, 4688.34]})
PT01 = json.dumps({"Sub-Total": [957.68, 957.68, -2649.95, -1692.27],
                   "Grand Total": [-5449.32, -5449.32, 4197.46, -1251.87]})
SEP30 = [dict(r, printed_totals=PT30) for r in SEP30]
OCT01 = [dict(r, printed_totals=PT01) for r in OCT01]


def _cells(block):
    return [(r["instrument"], r["tenor"], r["offered"], r["accepted"], r["rate"], r["maturity"], r["net"])
            for r in block["rows"]]


def test_30_sep_matches_the_desks_sheet_exactly():
    got = _cells(day_block(SEP30, datetime.date(2026, 9, 30)))
    assert got == [
        ("IBLF", "7-Days", 610.94, 610.94, "3.00-4.14", -55.14, 555.80),
        ("AR", "1-Day", 1651.63, 1651.63, 9.5, 0.0, 1651.63),
        ("Sub-Total", "", 2262.57, 2262.57, None, -55.14, 2207.43),
        ("SDF", "1-Day", -6846.0, -6846.0, 7.5, 9326.92, 2480.92),
        ("Grand Total", "", -4583.43, -4583.43, None, 9271.77, 4688.34),
    ]


def test_1_oct_uses_bbs_printed_net_not_our_subtraction():
    """957.68 - 997.89 is (40.21); BB printed (40.22) and the desk's sheet carries BB's figure."""
    got = _cells(day_block(OCT01, datetime.date(2026, 10, 1)))
    assert got[0] == ("IBLF", "7-Days", 957.68, 957.68, "3.00-5.25", -997.89, -40.22)
    assert got[1] == ("AR", "1-Day", 0.0, 0.0, 9.5, -1652.05, -1652.05)
    assert got[2] == ("Sub-Total", "", 957.68, 957.68, None, -2649.95, -1692.27), "BB's -2,649.95, not our -2,649.94"
    assert got[4] == ("Grand Total", "", -5449.32, -5449.32, None, 4197.46, -1251.87), "BB's printed total, not -1,251.86"


def test_a_line_we_have_not_read_is_blank_and_flagged_never_zero():
    """The unread LINE stays blank. Without BB's printed totals the sum over it would look complete, so the total
    is blank too; with them, BB's own figure shows (it is BB's, not a sum of ours)."""
    rows = [dict(SEP30[0], offered_bdt_crore=None, net_bdt_crore=None)] + SEP30[1:]
    b = day_block(rows, datetime.date(2026, 9, 30))
    assert b["unread"] is True
    first = b["rows"][0]
    assert first["offered"] is None and first["net"] is None
    bare = day_block([dict(r, printed_totals=None) for r in rows], datetime.date(2026, 9, 30))
    sub = next(r for r in bare["rows"] if r["instrument"] == "Sub-Total")
    assert sub["offered"] is None and sub["net"] is None, "a partial sum would look complete"
    assert sub["accepted"] == 2262.57, "columns we do hold still add up"


def test_the_workbook_reproduces_the_layout():
    xl = build_daily_sheet([day_block(SEP30, datetime.date(2026, 9, 30))])
    ws = load_workbook(io.BytesIO(xl)).active
    assert ws["A1"].value == "30-Sep-26"
    assert [c.value for c in ws[3]][:2] == ["Instruments", "Tenor"]
    assert ws["G3"].value.startswith("Net injection(+) / absorption(-)")
    assert ws["A4"].value == "IBLF" and ws["E4"].value == "3.00-4.14" and ws["F4"].value == -55.14
    assert ws["A8"].value == "Grand Total" and ws["G8"].value == 4688.34
    assert ws["C4"].number_format == ACCOUNTING and ws["A4"].fill.fgColor.rgb.endswith("FFFF00")
    assert ws["E5"].value == 9.5 and ws["E5"].number_format == "0.00"


def test_two_days_stack_and_a_day_with_no_absorption_has_no_subtotal():
    b1, b2 = day_block(SEP30, datetime.date(2026, 9, 30)), day_block([SEP30[0]], datetime.date(2026, 10, 1))
    assert [r["instrument"] for r in b2["rows"]] == ["IBLF", "Grand Total"]
    ws = load_workbook(io.BytesIO(build_daily_sheet([b1, b2]))).active
    assert [c.value for c in ws["A"] if c.value in ("30-Sep-26", "01-Oct-26")] == ["30-Sep-26", "01-Oct-26"]


def test_instrument_labels_come_from_the_key_not_a_list():
    from omo_sheet_logic import instrument_label
    assert instrument_label("CB_REPO") == "CB Repo" and instrument_label("IBLF") == "IBLF"


def test_the_parser_reads_bbs_printed_total_rows():
    from fetchers.omo import _extract_printed_totals
    got = json.loads(_extract_printed_totals(
        "x\nSub-Total 2,262.57 2,262.57 -55.14 2,207.43\nGrand Total -4,583.43 -4,583.43 9,271.77 4,688.34\nNet"))
    assert got == {"Sub-Total": [2262.57, 2262.57, -55.14, 2207.43],
                   "Grand Total": [-4583.43, -4583.43, 9271.77, 4688.34]}
    assert _extract_printed_totals("no totals here") is None


def test_a_printed_total_that_disagrees_with_the_lines_is_flagged_not_trusted():
    """If BB's total says 5,000 and our lines say 2,262.57, a line is missing: show the sum and say so."""
    bad = json.dumps({"Sub-Total": [5000.0, 5000.0, -55.14, 2207.43]})
    b = day_block([dict(r, printed_totals=bad) for r in SEP30], datetime.date(2026, 9, 30))
    sub = next(r for r in b["rows"] if r["instrument"] == "Sub-Total")
    assert sub["accepted"] == 2262.57 and b["mismatch"] == ["Sub-Total offered", "Sub-Total accepted"]
    assert sub["net"] == 2207.43, "the columns that do agree still carry BB's figure"


def test_the_sheet_shows_the_tenor_bb_printed_even_when_the_stored_tenor_was_corrected():
    """25-Oct CB Repo: BB printed 7-Days; its own later maturity print proved 2 days, so tenor_days is 2. The desk's sheet is
    BB's table, so it still reads 7-Days."""
    row = dict(R(1, "CB_REPO", 2, 15861.75, 15861.75, 9.5, 10009.01, 5852.73, "INJECTION"), tenor_label="7D")
    b = day_block([row], datetime.date(2026, 10, 25))
    assert b["rows"][0]["tenor"] == "7-Days"
