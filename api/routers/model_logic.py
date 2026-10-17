"""The liquidity model: a workbook that recalculates, not a picture of one.

Every other export here is a report — the numbers as they stand, formatted and
dated. This is the thing the desk asked for instead: a model it can drive.
Change the rollover assumption and the whole forward ladder moves, in Excel,
offline, with no call back to this API.

The division that makes it trustworthy:

  * **Actuals are values.** Anything BB has published — OMO deals and
    maturities, auction results, coupons and redemptions already scheduled —
    is written as a number, with its date. Those cells are not formulas and
    are not driven by any assumption.
  * **Projections are formulas over named assumptions.** Every forward figure
    is an Excel expression referring to a cell on the Assumptions sheet. There
    is no number anywhere in this workbook that came from a projection baked
    in at build time, because such a number cannot be checked, cannot be
    changed and looks exactly like an actual.

So if a cell has a formula, it is an opinion, and the opinion's inputs are all
on one sheet. If a cell has a number, BB published it.

Self-contained: the deployed API is rooted at api/ and cannot import anything
above it.
"""
import datetime
import io
from typing import Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

MONEY = "#,##0.00"
RATE = "0.0000"
PCT = "0.0%"
DATE = "yyyy-mm-dd"

_HDR_FILL = PatternFill("solid", fgColor="1F2937")
_HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
_TITLE = Font(bold=True, size=13)
_LABEL = Font(bold=True, size=10)
_INPUT_FILL = PatternFill("solid", fgColor="FEF3C7")      # the cells to edit
_FORMULA_FONT = Font(size=10, italic=True, color="1D4ED8")
_THIN = Side(style="thin", color="D1D5DB")

# The editable inputs. Each is (key, label, value, number format, note) and
# lands in a named cell so every formula in the workbook reads it by name —
# an assumption referenced by address is one nobody can find again.
ASSUMPTIONS: Tuple[tuple, ...] = (
    ("omo_rollover", "OMO rollover ratio",
     0.90, PCT,
     "Share of each maturing OMO tranche BB is assumed to roll. 1.0 = rolled "
     "in full (no liquidity effect); 0 = all of it drains."),
    ("auction_cover", "Auction acceptance vs notified",
     1.00, PCT,
     "How much of a notified auction is assumed accepted. BB regularly accepts "
     "more or less than it notified."),
    ("bill_price", "T-Bill price per 100 face",
     98.00, MONEY,
     "Bills sell at a discount, so cash paid is below face. Used only where BB "
     "has not published the sale value."),
    ("bond_price", "T-Bond price per 100 face",
     100.00, MONEY,
     "A premium re-issue sells above par. Used only where BB has not published "
     "the sale value."),
    # No rate-stress input. This model's cash is PRICE driven, not yield
    # driven, so a basis-point stress would have to be converted to a price
    # through a formula with a day-count approximation buried in it — and an
    # input that silently does nothing, or does something no one can see, is
    # worse than an input that is absent. Stress the prices directly.
)


def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill, cell.font = _HDR_FILL, _HDR_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _widths(ws, widths: Sequence[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _assumptions_sheet(wb: Workbook) -> Dict[str, str]:
    """Write the inputs and return {key: 'Assumptions'!$B$n} for the formulas."""
    ws = wb.create_sheet("Assumptions")
    ws["A1"] = "Assumptions — edit the shaded cells"
    ws["A1"].font = _TITLE
    ws["A2"] = ("Every forward figure in this workbook is a formula over these cells. "
                "Change one and the Model sheet recalculates. Nothing here affects a "
                "number BB has already published.")
    ws["A2"].alignment = Alignment(wrap_text=True)
    ws.merge_cells("A2:D2")
    ws.row_dimensions[2].height = 30

    ws["A4"], ws["B4"], ws["C4"] = "Input", "Value", "What it does"
    _style_header(ws, 4, 3)

    refs: Dict[str, str] = {}
    r = 5
    for key, label, value, fmt, note in ASSUMPTIONS:
        ws.cell(row=r, column=1, value=label).font = _LABEL
        cell = ws.cell(row=r, column=2, value=value)
        cell.number_format = fmt
        cell.fill = _INPUT_FILL
        cell.border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
        ws.cell(row=r, column=3, value=note).alignment = Alignment(wrap_text=True)
        ws.row_dimensions[r].height = 28
        refs[key] = f"Assumptions!$B${r}"
        r += 1

    _widths(ws, [34, 14, 78])
    return refs


def _actuals_sheet(wb: Workbook, name: str, headers: Sequence[str],
                   rows: Sequence[Sequence], note: str,
                   formats: Optional[Dict[int, str]] = None) -> None:
    """Published figures, as values. No formulas, no assumptions."""
    ws = wb.create_sheet(name)
    ws["A1"] = note
    ws["A1"].alignment = Alignment(wrap_text=True)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(3, len(headers)))
    ws.row_dimensions[1].height = 28
    for i, h in enumerate(headers, start=1):
        ws.cell(row=2, column=i, value=h)
    _style_header(ws, 2, len(headers))
    for ri, row in enumerate(rows, start=3):
        for ci, v in enumerate(row, start=1):
            # An unknown stays an EMPTY CELL. A zero is a number someone sums.
            cell = ws.cell(row=ri, column=ci, value=v if v is not None else None)
            if formats and ci in formats:
                cell.number_format = formats[ci]
    if rows:
        ws.auto_filter.ref = f"A2:{get_column_letter(len(headers))}{len(rows) + 2}"
    _widths(ws, [13] + [15] * (len(headers) - 1))


def build_model(data: dict, today: datetime.date = None) -> bytes:
    """Assemble the workbook. `data` is read-only input from the DB."""
    today = today or datetime.date.today()
    wb = Workbook()
    wb.remove(wb.active)

    refs = _assumptions_sheet(wb)

    # ── actuals ──────────────────────────────────────────────────────────────
    _actuals_sheet(
        wb, "OMO", ("Date", "Instrument", "Dealt (cr)", "Maturing (cr)", "Rate %"),
        [(r["date"], r["instrument"], r["dealt"], r["maturing"], r["rate"])
         for r in data["omo"]],
        "OMO operations as BB published them. Dealt adds liquidity on the deal "
        "date; the same tranche removes it again at maturity — one tranche, two "
        "opposite movements. Values, not formulas.",
        formats={1: DATE, 3: MONEY, 4: MONEY, 5: RATE})

    _actuals_sheet(
        wb, "Auctions", ("Settlement", "Type", "Tenor", "Notified (cr)",
                         "Accepted face (cr)", "Sale value (cr)", "Cut-off %", "Status"),
        [(r["settlement"], r["type"], r["tenor"], r["offered"], r["accepted"],
          r["sale"], r["cutoff"], r["status"]) for r in data["auctions"]],
        "Auction settlements. CONFIRMED rows carry BB's published figures; "
        "PLANNED rows carry BB's notified amount and no cut-off, because the "
        "auction has not happened. Sale value is the cash actually paid and is "
        "blank where BB did not publish it.",
        formats={1: DATE, 4: MONEY, 5: MONEY, 6: MONEY, 7: RATE})

    _actuals_sheet(
        wb, "Govt flows", ("Date", "Coupon in (mn)", "Principal in (mn)",
                           "Buyback in (mn)"),
        [(r["date"], r["coupon"], r["principal"], r["buyback"]) for r in data["flows"]],
        "Government payments INTO the market: coupons, redemptions and buyback "
        "cash. In BDT million, as the ladder stores them.",
        formats={1: DATE, 2: MONEY, 3: MONEY, 4: MONEY})

    # ── the model: formulas only ─────────────────────────────────────────────
    ws = wb.create_sheet("Model", 0)
    ws["A1"] = "Daily liquidity model"
    ws["A1"].font = _TITLE
    ws["A2"] = (f"Built {today}. Every figure below is an Excel formula over the "
                f"Assumptions sheet and the three data sheets — change an assumption "
                f"and this recalculates. BDT crore.")
    ws["A2"].alignment = Alignment(wrap_text=True)
    ws.merge_cells("A2:H2")
    ws.row_dimensions[2].height = 30

    headers = ("Date", "OMO dealt", "OMO maturing", "OMO net (after rollover)",
               "Auction cash out", "Govt cash in", "Net liquidity", "Cumulative")
    for i, h in enumerate(headers, start=1):
        ws.cell(row=4, column=i, value=h)
    _style_header(ws, 4, len(headers))

    dates = data["dates"]
    for n, d in enumerate(dates):
        r = 5 + n
        ws.cell(row=r, column=1, value=d).number_format = DATE
        A = f"$A{r}"
        # Actuals pulled by date from the data sheets.
        ws.cell(row=r, column=2, value=f"=SUMIFS(OMO!$C:$C,OMO!$A:$A,{A})")
        ws.cell(row=r, column=3, value=f"=SUMIFS(OMO!$D:$D,OMO!$A:$A,{A})")
        # The only modelled OMO line: a maturing tranche drains only the part
        # BB does not roll.
        ws.cell(row=r, column=4,
                value=f"=$B{r}-$C{r}*(1-{refs['omo_rollover']})")
        # Auction cash: the published sale value when there is one, else face
        # at the assumed price, else the notified amount at the assumed
        # acceptance and price. Each term is visible and editable.
        ws.cell(row=r, column=5, value=(
            f"=SUMIFS(Auctions!$F:$F,Auctions!$A:$A,{A})"
            f"+SUMIFS(Auctions!$E:$E,Auctions!$A:$A,{A},Auctions!$F:$F,\"\")"
            f"*IF(SUMIFS(Auctions!$E:$E,Auctions!$A:$A,{A},Auctions!$B:$B,\"T_BILL\")>0,"
            f"{refs['bill_price']},{refs['bond_price']})/100"
            f"+SUMIFS(Auctions!$D:$D,Auctions!$A:$A,{A},Auctions!$H:$H,\"PLANNED\")"
            f"*{refs['auction_cover']}"
            f"*{refs['bond_price']}/100"))
        # Government payments in, converted from million to crore.
        ws.cell(row=r, column=6, value=(
            f"=(SUMIFS('Govt flows'!$B:$B,'Govt flows'!$A:$A,{A})"
            f"+SUMIFS('Govt flows'!$C:$C,'Govt flows'!$A:$A,{A})"
            f"+SUMIFS('Govt flows'!$D:$D,'Govt flows'!$A:$A,{A}))/10"))
        ws.cell(row=r, column=7, value=f"=$D{r}-$E{r}+$F{r}")
        ws.cell(row=r, column=8,
                value=f"=$G{r}" if n == 0 else f"=$H{r - 1}+$G{r}")
        for c in range(2, 9):
            cell = ws.cell(row=r, column=c)
            cell.number_format = MONEY
            cell.font = _FORMULA_FONT

    last = 4 + len(dates)
    if dates:
        ws.cell(row=last + 2, column=1, value="Totals").font = _LABEL
        for c in range(2, 8):
            col = get_column_letter(c)
            t = ws.cell(row=last + 2, column=c, value=f"=SUM({col}5:{col}{last})")
            t.number_format, t.font = MONEY, _LABEL
    _widths(ws, [13, 15, 15, 23, 18, 15, 16, 16])

    # ── cover ────────────────────────────────────────────────────────────────
    cover = wb.create_sheet("Read me", 0)
    cover["A1"] = "Liquidity model — read me"
    cover["A1"].font = _TITLE
    lines = [
        ("Generated", str(today)),
        ("Window", f"{dates[0]} to {dates[-1]}" if dates else "no dates in range"),
        ("Unit", "BDT crore on Model and OMO/Auctions; BDT million on Govt flows, "
                 "converted by the model"),
        ("", ""),
        ("How to use it", "Edit the shaded cells on Assumptions. Everything on Model "
                          "is a formula and will recalculate."),
        ("What is a value", "Anything BB has published: OMO deals and maturities, "
                            "auction results, scheduled coupons and redemptions. These "
                            "cells are not driven by any assumption."),
        ("What is a formula", "Every forward figure. If a cell has a formula it is an "
                              "opinion, and all of the opinion's inputs are on one "
                              "sheet."),
        ("Blank cells", "A figure BB has not published is left empty, never zero. A "
                        "zero is a number someone will sum."),
        ("OMO sign", "A tranche moves liquidity twice — in on the deal date, out at "
                     "maturity. The rollover assumption applies only to the maturity "
                     "leg."),
        ("Auction cash", "The published sale value where BB printed one; otherwise "
                         "face value at the assumed price. The two differ on every "
                         "auction, so the distinction is kept visible."),
    ]
    r = 3
    for k, v in lines:
        if k:
            cover.cell(row=r, column=1, value=k).font = _LABEL
        cover.cell(row=r, column=2, value=v).alignment = Alignment(wrap_text=True)
        cover.row_dimensions[r].height = 30 if len(str(v)) > 70 else 16
        r += 1
    _widths(cover, [20, 96])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def model_data(session, date_from: datetime.date, date_to: datetime.date) -> dict:
    """Read the actuals the model is built on. No projection happens here."""
    from sqlalchemy import text

    omo = [{"date": r[0], "instrument": r[1], "dealt": float(r[2] or 0) or None,
            "maturing": float(r[3] or 0) or None,
            "rate": float(r[4]) if r[4] is not None else None}
           for r in session.execute(text("""
               SELECT transaction_date, instrument, accepted_bdt_crore,
                      maturity_bdt_crore, rate_pct
               FROM omo_transactions
               WHERE transaction_date BETWEEN :lo AND :hi
               ORDER BY transaction_date, instrument
           """), {"lo": str(date_from), "hi": str(date_to)}).fetchall()]

    auctions = [{"settlement": r[0], "type": r[1], "tenor": r[2],
                 "offered": float(r[3]) if r[3] is not None else None,
                 "accepted": float(r[4]) if r[4] is not None else None,
                 "sale": float(r[5]) if r[5] is not None else None,
                 "cutoff": float(r[6]) if r[6] is not None else None,
                 "status": r[7]}
                for r in session.execute(text("""
                    SELECT settlement_date, security_type, tenor_label,
                           offered_amount_bdt_crore, accepted_amount_bdt_crore,
                           sale_value_bdt_crore, weighted_avg_yield_pct, outflow_status
                    FROM auction_events
                    WHERE settlement_date BETWEEN :lo AND :hi
                    ORDER BY settlement_date, security_type, tenor_label
                """), {"lo": str(date_from), "hi": str(date_to)}).fetchall()]

    flows = [{"date": r[0], "coupon": float(r[1] or 0) or None,
              "principal": float(r[2] or 0) or None, "buyback": float(r[3] or 0) or None}
             for r in session.execute(text("""
                 SELECT flow_date, coupon_inflow_bdt_mill, principal_inflow_bdt_mill,
                        COALESCE(buyback_inflow_bdt_mill, 0)
                 FROM daily_net_flow
                 WHERE flow_date BETWEEN :lo AND :hi
                 ORDER BY flow_date
             """), {"lo": str(date_from), "hi": str(date_to)}).fetchall()]

    seen = sorted({r["date"] for r in omo}
                  | {r["settlement"] for r in auctions}
                  | {r["date"] for r in flows})
    return {"omo": omo, "auctions": auctions, "flows": flows, "dates": seen}
