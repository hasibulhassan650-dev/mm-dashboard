from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("/news-check")
def get_news_check():
    """Dollar sales the press reports versus BB's FX auction table (news is shown, never stored as BB data)."""
    import datetime
    from .reconcile_logic import reconcile_fx_news      # lazy: tests import routers top-level
    session = get_session()
    try:
        rows = reconcile_fx_news(session, datetime.date.today())
        return {"checks": [r for r in rows if r["verdict"] != "MATCH"],
                "matched": sum(1 for r in rows if r["verdict"] == "MATCH")}
    except Exception:
        session.rollback()
        return {"checks": [], "matched": 0, "error": "could not be computed"}
    finally:
        session.close()


@router.get("")
def get_fx_auctions(days: int = Query(365, ge=0, le=3650)):
    """All FX auction results ordered by auction date desc. days=0 returns all stored history."""
    import datetime
    session = get_session()
    try:
        if days == 0:
            rows = session.execute(text("""
                SELECT auction_date, settlement_date, auction_type,
                       num_bids, bid_amount_usd_mill, bid_range,
                       num_accepted, accepted_amount_usd_mill,
                       cutoff_rate, weighted_avg_rate
                FROM fx_auction_results
                ORDER BY auction_date DESC
            """)).fetchall()
        else:
            since = datetime.date.today() - datetime.timedelta(days=days)
            rows = session.execute(text("""
                SELECT auction_date, settlement_date, auction_type,
                       num_bids, bid_amount_usd_mill, bid_range,
                       num_accepted, accepted_amount_usd_mill,
                       cutoff_rate, weighted_avg_rate
                FROM fx_auction_results
                WHERE auction_date >= :since
                ORDER BY auction_date DESC
            """), {"since": str(since)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
