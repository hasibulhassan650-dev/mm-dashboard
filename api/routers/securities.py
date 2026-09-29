from fastapi import APIRouter, Query
from sqlalchemy import text
from typing import Optional
from db import get_session

router = APIRouter()


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
def get_auctions(months: int = Query(6, ge=1, le=24)):
    """Recent auction events."""
    import datetime
    since = datetime.date.today() - datetime.timedelta(days=months * 30)
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT fiscal_year, auction_no, auction_date, settlement_date,
                   security_type, tenor_label, offered_amount_bdt_crore,
                   accepted_amount_bdt_crore, weighted_avg_yield_pct, outflow_status
            FROM auction_events
            WHERE auction_date >= :since
            ORDER BY auction_date DESC
        """), {"since": str(since)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


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
