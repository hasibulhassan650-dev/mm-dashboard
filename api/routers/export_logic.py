"""Workbook builder for the report exports.

Why server-side. The frontend's `xlsx@0.18.5` (SheetJS CE) can set column widths
and number formats but cannot do bold headers, frozen panes or autofilter --
those need the paid build -- so a client-side export is a grid of raw cells.
openpyxl does all of it and is already a dependency, so the reports that get
shared or pivoted are built here instead.

Two rules the whole thing exists to enforce:

  * An unknown value is written as an EMPTY CELL, never 0. A zero in a
    spreadsheet is a number someone will sum, and "BB has not published an
    auction calendar for this month" summed as zero becomes "no borrowing".
  * Every workbook carries a cover sheet naming the units, the window, when it
    was generated and where each series stops. A sheet of numbers with no
    provenance is unauditable the moment it leaves the browser.

Self-contained: the deployed API is rooted at api/ and cannot import anything
above it.
"""
import datetime
import io
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MONEY = "#,##0.00"
INTEGER = "#,##0"
RATE = "0.0000"
DATE = "yyyy-mm-dd"

_HEADER_FILL = PatternFill("solid", fgColor="1F2937")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_TITLE_FONT = Font(bold=True, size=13)
_LABEL_FONT = Font(bold=True, size=10)

# Columns whose numbers are rates/percentages rather than money, matched on the
# header text so a caller does not have to pass a schema.
_RATE_HINTS = ("rate", "yield", "%", "pct")
_COUNT_HINTS = ("count", "months", "deals", "rows", "no.")


def _fmt_for(header: str, sample: Any) -> Optional[str]:
    h = header.lower()
    if isinstance(sample, (datetime.date, datetime.datetime)):
        return DATE
    if isinstance(sample, bool) or not isinstance(sample, (int, float)):
        return None
    if any(k in h for k in _RATE_HINTS):
        return RATE
    if any(k in h for k in _COUNT_HINTS):
        return INTEGER
    return MONEY


def _write_sheet(wb: Workbook, name: str, rows: Sequence[Dict[str, Any]],
                 note: Optional[str] = None) -> None:
    # Excel rejects a sheet name over 31 chars or containing []:*?/\
    safe = "".join(" " if c in "[]:*?/\\" else c for c in name)[:31] or "Sheet"
    ws = wb.create_sheet(safe)
    if not rows:
        ws["A1"] = note or "No rows for this window."
        ws["A1"].font = Font(italic=True, color="6B7280")
        ws.column_dimensions["A"].width = 60
        return

    headers: List[str] = []
    for r in rows:
        for k in r:
            if k not in headers:
                headers.append(k)

    first_data_row = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="6B7280")
        first_data_row = 3

    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=first_data_row, column=c, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # One representative non-None value per column decides its number format.
    sample: Dict[str, Any] = {}
    for h in headers:
        for r in rows:
            if r.get(h) is not None:
                sample[h] = r[h]
                break

    for i, r in enumerate(rows, start=first_data_row + 1):
        for c, h in enumerate(headers, start=1):
            v = r.get(h)
            # None stays None: openpyxl writes a genuinely empty cell, which is
            # what "not published" has to look like in a spreadsheet.
            cell = ws.cell(row=i, column=c, value=v)
            fmt = _fmt_for(h, sample.get(h))
            if fmt and isinstance(v, (int, float)) and not isinstance(v, bool):
                cell.number_format = fmt

    for c, h in enumerate(headers, start=1):
        widest = max([len(str(h))] + [len(f"{r.get(h):,.2f}") if isinstance(r.get(h), float)
                                      else len(str(r.get(h) or "")) for r in rows[:400]])
        ws.column_dimensions[get_column_letter(c)].width = min(max(widest + 3, 11), 42)

    ws.freeze_panes = ws.cell(row=first_data_row + 1, column=2)
    ws.auto_filter.ref = (f"A{first_data_row}:"
                          f"{get_column_letter(len(headers))}{first_data_row + len(rows)}")


def _write_cover(wb: Workbook, title: str, facts: Sequence[Tuple[str, Any]],
                 caveats: Sequence[str]) -> None:
    ws = wb.create_sheet("About this export", 0)
    ws["A1"] = title
    ws["A1"].font = _TITLE_FONT
    r = 3
    for label, value in facts:
        ws.cell(row=r, column=1, value=label).font = _LABEL_FONT
        ws.cell(row=r, column=2, value="" if value is None else str(value))
        r += 1
    if caveats:
        r += 1
        ws.cell(row=r, column=1, value="Read this before using the numbers").font = _LABEL_FONT
        r += 1
        for c in caveats:
            cell = ws.cell(row=r, column=1, value="•  " + c)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.row_dimensions[r].height = 30
            r += 1
    ws.column_dimensions["A"].width = 34
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18


def build_workbook(title: str, sheets: Sequence[Tuple[str, Sequence[Dict[str, Any]]]],
                   facts: Sequence[Tuple[str, Any]] = (),
                   caveats: Sequence[str] = (),
                   notes: Optional[Dict[str, str]] = None) -> bytes:
    """A formatted workbook: cover sheet first, then each data sheet."""
    wb = Workbook()
    wb.remove(wb.active)                      # drop the default empty sheet
    _write_cover(wb, title, list(facts) + [("Generated (UTC)",
                                            datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))],
                 caveats)
    for name, rows in sheets:
        _write_sheet(wb, name, rows, (notes or {}).get(name))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(stem: str, *parts: Any) -> str:
    bits = [stem] + [str(p) for p in parts if p]
    return "_".join(bits).replace(" ", "_") + ".xlsx"
