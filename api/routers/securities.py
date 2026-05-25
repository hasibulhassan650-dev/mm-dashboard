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
