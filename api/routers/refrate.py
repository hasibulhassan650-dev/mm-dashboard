from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_refrates(days: int = Query(90, ge=0, le=3650)):
    """DOMMR and BOFR reference rates. days=0 returns all stored history."""
    import datetime
    session = get_session()
    try:
        if days == 0:
            rows = session.execute(text("""
                SELECT trade_date, rate_type, product, amount_crore, rate_pct, num_deals
                FROM ref_rates
                ORDER BY trade_date DESC, rate_type, product
            """)).fetchall()
        else:
            since = datetime.date.today() - datetime.timedelta(days=days)
            rows = session.execute(text("""
                SELECT trade_date, rate_type, product, amount_crore, rate_pct, num_deals
                FROM ref_rates
                WHERE trade_date >= :since
                ORDER BY trade_date DESC, rate_type, product
            """), {"since": str(since)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
