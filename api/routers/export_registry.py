"""Every exportable dataset, declared once, with the dates it actually covers.

The exports were real but scattered: a Download button on each page and no
answer to "what can I get out of this, and for which dates?" without visiting
six pages and reading six date pickers. So this declares them — what each one
contains, how far back the data goes, what the date arguments are called — and
a test asserts that every `/export` route in the API appears here. A new export
that nobody can find is the same failure as a page missing from the nav, which
this project has already had once.

`coverage` is read from the database at request time, not written down: an
export's earliest and latest row is a fact that changes daily, and a hardcoded
answer would go stale and mislead in exactly the way the rest of this work
exists to prevent.
"""
import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter
from sqlalchemy import text

from db import get_session

router = APIRouter()


class Export:
    """One downloadable workbook.

    `path` is the API route. `args` names the query parameters it accepts, so
    the page can build a URL without a second list of the same knowledge.
    `table`/`date_column` are what its coverage is measured from — the data
    behind the file, not the file itself.
    """

    def __init__(self, key: str, label: str, path: str, what: str,
                 args: List[str], table: Optional[str] = None,
                 date_column: Optional[str] = None, sheets: str = "",
                 unit: str = "BDT crore"):
        self.key, self.label, self.path, self.what = key, label, path, what
        self.args, self.table, self.date_column = args, table, date_column
        self.sheets, self.unit = sheets, unit


EXPORTS: List[Export] = [
    Export("auction_sheet", "Auction Data (your sheet format)",
           "/api/securities/auction-sheet/export",
           "One row per auction in the eight columns of the desk's own "
           "spreadsheet: ISIN, issue and maturity dates, tenure, auctioned "
           "amount, successful market bid, devolvement, coupon. A re-issue "
           "appears once per auction, which is correct — the same ISIN sold "
           "again at its own cut-off.",
           ["date_from", "date_to"],
           "primary_yield_snapshots", "auction_date",
           sheets="Auctions · Notes"),
    Export("auctions", "Treasury Auctions (bids and cover)",
           "/api/securities/auctions/export",
           "Every auction with bids received and accepted, the cover ratio, "
           "yield ranges, weighted-average price and the devolvement yield.",
           ["months", "date_from", "date_to"],
           "primary_yield_snapshots", "auction_date",
           sheets="Auctions · Summary"),
    Export("schedule", "Redemption & Coupon Schedule by product",
           "/api/flows/by-product/export",
           "Forward monthly redemptions, coupons and auction settlements split "
           "T-Bond / T-Bill / FRTB, with Bangladesh fiscal-year subtotals, plus "
           "the event detail behind every month so any figure can be traced to "
           "the securities paying it.",
           ["years"],
           "maturity_events", "payment_date",
           sheets="Monthly · Detail · FY subtotals"),
    Export("ladder", "Liquidity Ladder (cash flows)",
           "/api/flows/forecast/export",
           "The daily cash ladder: coupon and principal inflows, buyback cash, "
           "auction outflows at the price actually paid, and the running net.",
           ["days", "date_from", "date_to"],
           "daily_net_flow", "flow_date",
           sheets="Ladder · Cumulative"),
    Export("islamic", "Islamic Window (BGIIB)",
           "/api/islamic/export",
           "Every BGIIB auction BB has published: bids, offered and accepted "
           "amounts, the profit-sharing ratio, weighted-average Expected Profit "
           "Rate and both rate ranges — with the comparable T-Bill cut-off and "
           "ITS own auction date beside each row, because BGIIB is auctioned on "
           "Tuesdays and T-Bills on Sundays and the two are never the same day.",
           ["weeks"],
           "bgiib_auctions", "auction_date",
           sheets="BGIIB auctions · By tenor"),
    Export("model", "Liquidity Model (live formulas)",
           "/api/flows/model/export",
           "A workbook that recalculates rather than a report. The Assumptions "
           "sheet holds the OMO rollover ratio, auction acceptance and the "
           "prices per 100 face; every forward figure on the Model sheet is an "
           "Excel formula over those cells, so changing one moves the whole "
           "ladder offline. Published figures are written as values and no "
           "assumption touches them — a formula is an opinion, a number is "
           "something BB printed.",
           ["days", "date_from", "date_to"],
           "daily_net_flow", "flow_date",
           sheets="Read me · Summary · Model · Assumptions · OMO · Auctions · Govt flows"),
    Export("liquidity_news", "Liquidity Watch (events read from the news)",
           "/api/news/export",
           "Government and Bangladesh Bank liquidity events reported in the Bangladeshi "
           "press that BB's own tables do not hold - subsidy releases, arrears, support to "
           "banks, reserve-requirement changes - each with the verbatim sentence it was "
           "read from, every source link and a flag for events BB's data already holds. "
           "Read by a language model and verified by code; never part of any ladder total.",
           ["days"],
           "liquidity_events", "event_date",
           sheets="Events · Monthly"),
    Export("omo_ladder", "OMO Maturity Ladder",
           "/api/omo/maturity-ladder/export",
           "Day-by-day OMO maturities with the product breakdown — every "
           "instrument BB operates, not a subset — and the net liquidity "
           "effect of each day's deals and maturities.",
           ["days", "date_from", "date_to"],
           "omo_transactions", "transaction_date",
           sheets="Ladder · By instrument"),
]

BY_KEY: Dict[str, Export] = {e.key: e for e in EXPORTS}


def _coverage(session, e: Export) -> dict:
    if not e.table or not e.date_column:
        return {}
    try:
        n, lo, hi = session.execute(text(
            f"SELECT COUNT(*), MIN({e.date_column}), MAX({e.date_column}) "  # noqa: S608
            f"FROM {e.table}")).fetchone()
    except Exception:
        session.rollback()
        return {"error": "the table behind this export could not be read"}
    return {"rows": n, "earliest": str(lo) if lo else None,
            "latest": str(hi) if hi else None}


@router.get("")
def get_exports():
    """Every export, with the dates it can actually cover right now."""
    session = get_session()
    try:
        today = datetime.date.today()
        out = []
        for e in EXPORTS:
            out.append({
                "key": e.key, "label": e.label, "path": e.path, "what": e.what,
                "args": e.args, "sheets": e.sheets, "unit": e.unit,
                "source_table": e.table, "date_column": e.date_column,
                "coverage": _coverage(session, e),
            })
        return {"as_of": str(today), "count": len(out), "exports": out}
    finally:
        session.close()
