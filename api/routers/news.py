"""Liquidity Watch: liquidity events reported in the news that BB's own data does not hold.

See news_logic.py. Nothing served here is a ladder input.
"""
from fastapi import APIRouter, Query
from fastapi.responses import Response

from db import get_session

router = APIRouter()

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("")
def get_news(days: int = Query(120, ge=7, le=800), redundant: bool = False, minor: bool = False):
    from .news_logic import news_block
    s = get_session()
    try:
        return news_block(s, days=days, show_redundant=redundant, show_minor=minor)
    finally:
        s.close()


@router.get("/export")
def export_news(days: int = Query(400, ge=7, le=800)):
    """Every news-derived liquidity event, with its quote and sources, as a workbook."""
    from .export_logic import build_workbook, filename
    from .news_logic import news_block
    s = get_session()
    try:
        p = news_block(s, days=days, show_redundant=True, show_minor=True, limit=5000)
    finally:
        s.close()
    rows = [{
        "Date": e["when"], "Kind": e["kind"], "Direction": e["direction"],
        "Amount (Cr)": e["amount_crore"], "Status": e["status"], "Channel": e["channel"],
        "Counterparty": e["counterparty"], "Summary": e["summary"], "Evidence (verbatim)": e["evidence_quote"],
        "Sources": " | ".join(x.get("url") or "" for x in e["sources"]),
        "Confidence": e["confidence"], "Matters": "yes" if e["matters"] else "no",
        "Already in BB data": e["redundant_with"] or "",
    } for e in p["events"]]
    monthly = [{"Month": m["month"], "Injected (Cr)": m["inject_crore"], "Absorbed (Cr)": m["absorb_crore"],
                "Events": m["events"]} for m in p["monthly"]]
    body = build_workbook(
        "Bangladesh liquidity events read from the news",
        [("Events", rows), ("Monthly", monthly)],
        facts=[("Window", f"last {days} days to {p['as_of']}"),
               ("Unit", "BDT crore"),
               ("Source", "Bangladeshi financial press, read by a language model and verified by code")],
        caveats=[p["note"],
                 "Events without a stated taka amount are listed but never counted as zero.",
                 "'Already in BB data' names the table that holds the same event; those rows are "
                 "hidden on the page by default so the same fact is not told twice."])
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{filename("bd_liquidity_news", p["as_of"])}"'})
