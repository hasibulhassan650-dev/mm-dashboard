from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from typing import Optional
from db import get_session

router = APIRouter()

XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("")
def get_securities(security_type: Optional[str] = None):
    """All securities with basic details."""
    session = get_session()
    try:
        q = """
            SELECT isin, security_name_norm, security_type, issue_date, maturity_date,
                   coupon_rate_pct, coupon_frequency, outstanding_bdt_mill
            FROM securities
        """
        params: dict = {}
        if security_type:
            q += " WHERE security_type = :stype"
            params["stype"] = security_type.upper()
        q += " ORDER BY maturity_date"
        rows = session.execute(text(q), params).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/auctions")
def get_auctions(months: int = Query(None, ge=1, le=240,
                                     description="Months back; the window runs forward to the "
                                                 "end of BB's published calendar"),
                 date_from: str = Query(None, description="YYYY-MM-DD"),
                 date_to: str = Query(None, description="YYYY-MM-DD")):
    """The auction book: every auction in the window with its results.

    Replaces a backward-only query that could not see PLANNED auctions at all --
    it filtered `auction_date >= since` with no forward reach, so BB's published
    calendar was invisible to the one endpoint meant to list auctions.

    Returns the itemised auctions plus per-product and monthly roll-ups, so the
    page needs a single request.
    """
    from .auction_logic import auction_book
    session = get_session()
    try:
        return auction_book(session, months=months, date_from=date_from, date_to=date_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        session.close()


@router.get("/auctions/export")
def export_auctions(months: int = Query(None, ge=1, le=240),
                    date_from: str = Query(None), date_to: str = Query(None)):
    """The auction book as a formatted workbook."""
    from .auction_logic import auction_book
    from .export_logic import build_workbook, filename
    session = get_session()
    try:
        p = auction_book(session, months=months, date_from=date_from, date_to=date_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        session.close()

    LABEL = {"T_BOND": "T-Bond", "T_BILL": "T-Bill", "FRTB": "FRTB", "OTHER": "Other"}
    book = [{
        "Auction Date": a["auction_date"], "Settlement Date": a["settlement_date"],
        "Fiscal Year": a["fiscal_year"], "Auction No": a["auction_no"],
        "Product": LABEL.get(a["product"], a["product"]), "Tenor": a["tenor_label"],
        "Notified (crore)": a["notified_crore"],
        "Bids Received (crore)": a["bids_crore"],
        "Accepted (crore)": a["accepted_crore"],
        "Cut-off Yield %": a["cutoff_yield_pct"],
        "Bid to Cover": a["bid_to_cover"],
        "Status": a["status"],
        "Results Published": "yes" if a["has_results"] else "no",
    } for a in p["auctions"]]

    category = [{
        "Product": LABEL.get(k, k),
        "Auctions": v["auctions"],
        "Confirmed": v["confirmed"], "Planned": v["planned"],
        "Notified (crore)": v["notified_crore"],
        "Accepted (crore)": v["accepted_crore"],
        "Accepted / Notified": v["accepted_share"],
        "Avg Cut-off % (accepted-weighted)": v["avg_cutoff_pct"],
    } for k, v in sorted(p["by_product"].items())]

    monthly = [{
        "Month": m["month"], "Auctions": m["auctions"],
        "Confirmed": m["confirmed"], "Planned": m["planned"],
        "Notified (crore)": m["notified_crore"],
        "Accepted (crore)": m["accepted_crore"],
        "Avg Cut-off % (accepted-weighted)": m["avg_cutoff_pct"],
    } for m in p["months"]]

    body = build_workbook(
        "Bangladesh Treasury Auctions",
        [("Category summary", category), ("Every auction", book),
         ("Monthly volume", monthly)],
        facts=[("Window", f"{p['from']} to {p['to']}"), ("Unit", "BDT crore"),
               ("As of", p["as_of"]),
               ("Auction calendar published to", p["calendar_published_to"]),
               ("Auctions in window", p["totals"]["auctions"]),
               ("Confirmed", p["totals"]["confirmed"]),
               ("Planned (calendar target only)", p["totals"]["planned"]),
               ("Source", "Bangladesh Bank — auction calendar and treasury results")],
        caveats=[
            "A PLANNED row is BB's calendar target, not a settled amount. Do not sum it "
            "together with CONFIRMED rows as if it had happened.",
            "'Notified' and 'Bids Received' are different numbers from different BB pages: "
            "notified is the amount BB offered, bids received is what the market put in. They "
            "are never merged here.",
            "Bid to Cover is blank where bids are unknown, and also where bids exactly equal "
            "accepted: in the earlier era BB published a fixed weekly target in the bids "
            "column, so a ratio of exactly 1.00 there is an artefact, not a covered auction.",
            f"Nothing is published beyond {p['calendar_published_to']}. The absence of rows "
            "after that date means BB has not announced the calendar, not that no auctions "
            "will be held — BB auctions bills nearly every week.",
        ],
        notes={"Every auction":
               "One row per auction. Cut-off yield and bids appear once BB publishes the "
               "results; a planned auction is listed with those columns blank."},
    )
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{filename("bd_treasury_auctions", p["from"], p["to"])}"'})


@router.get("/next-auction")
def get_next_auction(limit: int = Query(3, ge=1, le=10)):
    """The next scheduled auction(s), read from BB's published calendar.

    The weekday pattern (bills Sunday, bonds Tuesday) is a HABIT, not a rule —
    the calendar is authoritative, so this reads auction_events rather than
    predicting from weekdays or from the median gap between past auctions.

    Two states matter and are reported distinctly:
      * `published=false` — BB has not published a calendar reaching today yet
        (true as of 30-Sep-2026: their page stops at 27-Sep). The UI must SAY
        so; rendering nothing would look like "no auction due", which is a
        different and much more dangerous claim.
      * `is_holiday=true` — the scheduled date falls on a known closure, so BB
        will move it. We flag the clash and do NOT invent the new date.
    """
    import datetime
    session = get_session()
    try:
        today = datetime.date.today()
        rows = session.execute(text("""
            SELECT auction_date, settlement_date, security_type, tenor_label,
                   offered_amount_bdt_crore, outflow_status
            FROM auction_events
            WHERE auction_date >= :today
            ORDER BY auction_date, tenor_label
        """), {"today": str(today)}).fetchall()

        holidays = {}
        for h in session.execute(text(
                "SELECT calendar_date, holiday_name FROM holiday_calendar "
                "WHERE holiday_type <> 'WORKING_DAY'")).fetchall():
            d = h[0] if isinstance(h[0], datetime.date) else datetime.date.fromisoformat(str(h[0])[:10])
            holidays[d] = h[1]

        by_date: dict = {}
        for r in rows:
            d = r[0] if isinstance(r[0], datetime.date) else datetime.date.fromisoformat(str(r[0])[:10])
            g = by_date.setdefault(d, {
                "auction_date": str(d),
                "settlement_date": str(r[1]) if r[1] else None,
                "tenors": [], "security_types": [], "offered_total_crore": 0.0,
                "days_away": (d - today).days,
                "is_holiday": d in holidays,
                "holiday_name": holidays.get(d),
            })
            if r[3] and r[3] not in g["tenors"]:
                g["tenors"].append(r[3])
            if r[2] and r[2] not in g["security_types"]:
                g["security_types"].append(r[2])
            g["offered_total_crore"] += float(r[4] or 0)

        upcoming = [by_date[d] for d in sorted(by_date)][:limit]
        last_cal = session.execute(text("SELECT MAX(auction_date) FROM auction_events")).scalar()

        return {
            "as_of": str(today),
            "calendar_through": str(last_cal) if last_cal else None,
            "published": bool(upcoming),
            "next": upcoming[0] if upcoming else None,
            "following": upcoming[1:],
        }
    finally:
        session.close()
