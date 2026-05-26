from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_callmoney(days: int = Query(30, ge=1, le=180)):
    """
    Returns two things:
    - daily_summary: one row per trade_date — overnight weighted avg rate, total volume, num deals
    - latest_breakdown: all product/maturity rows for the most recent date
    """
    import datetime
    since = datetime.date.today() - datetime.timedelta(days=days)
    session = get_session()
    try:
        # Daily summary: overnight weighted average rate + total volume across all products
        daily = session.execute(text("""
            SELECT
                trade_date,
                SUM(amount_crore)                                            AS total_volume_crore,
                SUM(num_deals)                                               AS total_deals,
                SUM(CASE WHEN product = 'Overnight' THEN amount_crore END)   AS overnight_volume_crore,
                SUM(CASE WHEN product = 'Overnight' THEN num_deals    END)   AS overnight_deals,
                -- volume-weighted average of overnight average_rate
                CASE WHEN SUM(CASE WHEN product = 'Overnight' THEN amount_crore END) > 0
                     THEN SUM(CASE WHEN product = 'Overnight' THEN average_rate_pct * amount_crore END)
                          / SUM(CASE WHEN product = 'Overnight' THEN amount_crore END)
                     ELSE NULL END                                            AS overnight_wavg_rate,
                MAX(CASE WHEN product = 'Overnight' THEN highest_rate_pct END) AS overnight_high,
                MIN(CASE WHEN product = 'Overnight' THEN lowest_rate_pct  END) AS overnight_low
            FROM call_money_rates
            WHERE trade_date >= :since
            GROUP BY trade_date
            ORDER BY trade_date
        """), {"since": str(since)}).fetchall()

        # Latest date full breakdown
        latest_date = session.execute(text(
            "SELECT MAX(trade_date) FROM call_money_rates"
        )).scalar()

        breakdown = []
        if latest_date:
            breakdown = session.execute(text("""
                SELECT trade_date, product, maturity_days,
                       amount_crore, highest_rate_pct, lowest_rate_pct,
                       average_rate_pct, num_deals
                FROM call_money_rates
                WHERE trade_date = :dt
                ORDER BY product, maturity_days
            """), {"dt": str(latest_date)}).fetchall()

        return {
            "daily_summary":    [dict(r._mapping) for r in daily],
            "latest_breakdown": [dict(r._mapping) for r in breakdown],
            "latest_date":      str(latest_date) if latest_date else None,
        }
    finally:
        session.close()
