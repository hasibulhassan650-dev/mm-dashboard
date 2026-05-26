from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_fx_auctions(days: int = Query(365, ge=1, le=1000)):
    """All FX auction results ordered by auction date desc."""
    import datetime
    since = datetime.date.today() - datetime.timedelta(days=days)
    session = get_session()
    try:
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
