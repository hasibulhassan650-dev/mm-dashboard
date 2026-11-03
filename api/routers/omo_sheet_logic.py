"""The desk's OMO daily sheet: BB's own table, day by day, ready to paste into another workbook.

One block per day, in the layout the desk already works in:

    30-Sep-26
    Instruments | Tenor | Offered | Accepted / Settlement | Rate (%) & PSR/EPR | Maturity | Net injection(+)/absorption(-)
    IBLF  7-Days   ...        <- injection lines, in the order BB prints them
    Sub-Total
    SDF   1-Day    ...        <- absorption lines (SDF)
    Grand Total

Signs follow the desk's sheet: an injection line is positive on offered/accepted and NEGATIVE on maturity (the
liquidity comes back to BB); an absorption line is the mirror image. Net is BB's printed figure where we hold it:
BB rounds from unrounded numbers, so "accepted + maturity" is sometimes a cent away from what it printed, and the
desk's sheet shows BB's.

An unknown value is a blank cell, never 0, and a day with a blank is flagged under its block.

Self-contained: the deployed API is rooted at api/ and cannot import anything above it.
"""
import datetime
import io
import json
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HEADERS = ["Instruments", "Tenor", "Offered Amount (Crore Taka)", "Accepted / Settlement Amount (Crore Taka)",
           "Rate (%) & PSR/EPR", "Maturity Amount (Crore Taka)", "Net injection(+) / absorption(-) (Crore Taka)"]
# accounting style: negatives in brackets, zero as a dash
TOTAL_TOLERANCE = 0.05          # BB's printed total vs the sum of its printed rows
ACCOUNTING = '#,##0.00_);(#,##0.00);"-"_)'


def instrument_label(key: str) -> str:
    """CB_REPO -> 'CB Repo'; IBLF stays IBLF. No list of products: the label comes from the key."""
    if "_" not in key:
        return key
    head, *rest = key.split("_")
    return " ".join([head.upper()] + [w.capitalize() for w in rest])


def tenor_label(days: Optional[int]) -> str:
    if days is None:
        return ""
    return f"{days}-Day" if days == 1 else f"{days}-Days"


def _rate_cell(rows: List[Dict[str, Any]]):
    """A single rate stays a number; a spread of rates is the text range BB prints ('3.00-4.14')."""
    lows, highs = [], []
    for r in rows:
        rng = r.get("rate_range")
        if rng and "-" in str(rng):
            lo, hi = str(rng).split("-", 1)
            try:
                lows.append(float(lo)); highs.append(float(hi))
                continue
            except ValueError:
                pass
        if r.get("rate_pct") is not None:
            lows.append(float(r["rate_pct"])); highs.append(float(r["rate_pct"]))
    if not lows:
        return None
    lo, hi = min(lows), max(highs)
    return round(lo, 2) if abs(hi - lo) < 0.005 else f"{lo:.2f}-{hi:.2f}"


def _total(lines: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Sum of a column, or None when any line is missing it: a partial sum would look complete."""
    vals = [ln[key] for ln in lines]
    if not vals or any(v is None for v in vals):
        return None
    return round(sum(vals), 2)


def day_block(rows: List[Dict[str, Any]], day: datetime.date) -> Dict[str, Any]:
    """The lines of one day. `rows` are that day's omo_transactions as dicts with: id, instrument, tenor_days,
    offered_bdt_crore, accepted_bdt_crore, maturity_bdt_crore, net_bdt_crore, rate_pct, rate_range, direction."""
    order: Dict[Any, List[Dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda x: x.get("id") or 0):          # insertion order is BB's print order
        order.setdefault((r["instrument"], r.get("tenor_days")), []).append(r)

    lines, unread = [], False
    for (inst, tenor), grp in order.items():
        absorb = grp[0].get("direction") == "ABSORPTION"
        sign = -1.0 if absorb else 1.0

        def col(name):
            vals = [g.get(name) for g in grp]
            return None if any(v is None for v in vals) else sum(abs(v) for v in vals)

        offered, accepted, maturity = col("offered_bdt_crore"), col("accepted_bdt_crore"), col("maturity_bdt_crore")
        nets = [g.get("net_bdt_crore") for g in grp]
        net = None if any(v is None for v in nets) else round(sum(nets), 2)
        if offered is None or net is None:
            unread = True
        lines.append({
            "kind": "line", "absorb": absorb, "instrument": instrument_label(inst), "tenor": tenor_label(tenor),
            "offered": None if offered is None else round(sign * offered, 2),
            "accepted": None if accepted is None else round(sign * accepted, 2),
            "rate": _rate_cell(grp),
            "maturity": None if maturity is None else round(-sign * maturity, 2),
            "net": net,
        })

    inj = [ln for ln in lines if not ln["absorb"]]
    mop = [ln for ln in lines if ln["absorb"]]

    printed = {}
    for r in sorted(rows, key=lambda x: x.get("id") or 0):
        if r.get("printed_totals"):
            try:
                printed = json.loads(r["printed_totals"])
            except (TypeError, ValueError):
                printed = {}
            break
    mismatch = []

    def total_row(label, group):
        summed = {k: _total(group, k) for k in ("offered", "accepted", "maturity", "net")}
        row = {"kind": "total", "instrument": label, "tenor": "", "rate": None, **summed}
        # BB adds UNROUNDED numbers, so its printed total can be a cent away from the sum of its rounded rows and
        # the desk's sheet carries BB's figure. Used only while it agrees with our lines to within 5 paise per
        # column: a bigger gap means a line is missing or wrong, which must be seen, not papered over.
        pt = printed.get(label)
        if pt and len(pt) == 4:
            for k, v in zip(("offered", "accepted", "maturity", "net"), pt):
                if summed[k] is not None and abs(summed[k] - v) > TOTAL_TOLERANCE:
                    mismatch.append(f"{label} {k}")
                else:
                    row[k] = round(v, 2)
        return row

    out = list(inj)
    if inj and mop:
        out.append(total_row("Sub-Total", inj))
    out.extend(mop)
    if lines:
        out.append(total_row("Grand Total", lines))
    return {"date": day, "rows": out, "unread": unread, "mismatch": mismatch}


def build_daily_sheet(blocks: List[Dict[str, Any]]) -> bytes:
    """One sheet, one block per day, in the desk's colours: a yellow date cell on a pale band, a bold yellow
    header, yellow body. No cover sheet: the file is meant to be copied from, cell for cell."""
    wb = Workbook()
    ws = wb.active
    ws.title = "OMO daily"
    yellow = PatternFill("solid", fgColor="FFFF00")
    band = PatternFill("solid", fgColor="E2EFDA")
    grey = PatternFill("solid", fgColor="F2F2F2")
    bold = Font(bold=True)
    note_font = Font(italic=True, color="C00000", size=9)

    r = 1
    for b in blocks:
        for c in range(1, 8):
            ws.cell(r, c).fill = band
            ws.cell(r + 1, c).fill = grey
        d = ws.cell(r, 1, f"{b['date']:%d-%b-%y}")
        d.fill, d.font = yellow, bold
        r += 2
        for c, h in enumerate(HEADERS, 1):
            cell = ws.cell(r, c, h)
            cell.fill, cell.font = yellow, bold
            cell.alignment = Alignment(horizontal="left" if c <= 2 else "right")
        r += 1
        for row in b["rows"]:
            vals = [row["instrument"], row["tenor"], row["offered"], row["accepted"], row["rate"],
                    row["maturity"], row["net"]]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v)
                cell.fill = yellow
                if c >= 3 and isinstance(v, (int, float)):
                    cell.number_format = "0.00" if c == 5 else ACCOUNTING
                if c == 5 and isinstance(v, str):
                    cell.alignment = Alignment(horizontal="left")
            r += 1
        if b["unread"]:
            ws.cell(r, 1, "Offered Amount / Net not read for some lines of this day yet - left blank, not guessed.").font = note_font
            r += 1
        if b.get("mismatch"):
            ws.cell(r, 1, "BB's printed total differs from the sum of the lines above (" + ", ".join(b["mismatch"])
                    + ") - a line may be missing; the lines' sum is shown.").font = note_font
            r += 1
        r += 2                                                      # a gap between days

    for col, w in zip("ABCDEFG", (14, 9, 30, 40, 22, 30, 44)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
