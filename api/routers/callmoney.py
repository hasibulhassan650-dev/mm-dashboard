from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_callmoney(days: int = Query(90, ge=0, le=3650)):
    """
    Returns daily_summary and latest_breakdown.
    days=0 returns all stored history.
    """
    import datetime
    session = get_session()
    try:
        if days == 0:
            where = ""
            params: dict = {}
        else:
            since = datetime.date.today() - datetime.timedelta(days=days)
            where = "WHERE trade_date >= :since"
            params = {"since": str(since)}

        daily = session.execute(text(f"""
            SELECT
                trade_date,
                SUM(amount_crore)                                            AS total_volume_crore,
                SUM(num_deals)                                               AS total_deals,
                SUM(CASE WHEN product = 'Overnight' THEN amount_crore END)   AS overnight_volume_crore,
                SUM(CASE WHEN product = 'Overnight' THEN num_deals    END)   AS overnight_deals,
                CASE WHEN SUM(CASE WHEN product = 'Overnight' THEN amount_crore END) > 0
                     THEN SUM(CASE WHEN product = 'Overnight' THEN average_rate_pct * amount_crore END)
                          / SUM(CASE WHEN product = 'Overnight' THEN amount_crore END)
                     ELSE NULL END                                            AS overnight_wavg_rate,
                MAX(CASE WHEN product = 'Overnight' THEN highest_rate_pct END) AS overnight_high,
                MIN(CASE WHEN product = 'Overnight' THEN lowest_rate_pct  END) AS overnight_low
            FROM call_money_rates
            {where}
            GROUP BY trade_date
            ORDER BY trade_date
        """), params).fetchall()

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
