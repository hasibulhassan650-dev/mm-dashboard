from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_refrates(days: int = Query(60, ge=1, le=400)):
    """DOMMR and BOFR reference rates, last N days."""
    import datetime
    since = datetime.date.today() - datetime.timedelta(days=days)
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT trade_date, rate_type, product, amount_crore, rate_pct, num_deals
            FROM ref_rates
            WHERE trade_date >= :since
            ORDER BY trade_date DESC, rate_type, product
        """), {"since": str(since)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
