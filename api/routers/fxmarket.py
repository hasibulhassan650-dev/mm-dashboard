from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()

_IBFX_COLS = """
    trade_date, segment, num_deals, volume_usd_mn,
    high_rate, low_rate, war_rate
"""
# mid = (bid + ask) / 2, computed here rather than stored: a derived column
# drifts away from the inputs it came from the moment one of them is corrected.
_RATE_COLS = """
    rate_date, published_date, currency, bid_rate, ask_rate, war_rate,
    (bid_rate + ask_rate) / 2.0 AS mid_rate
"""


@router.get("")
def get_interbank_fx(days: int = Query(365, ge=0, le=3650)):
    """Interbank FX turnover by segment (SPOT / FORWARD / SWAP), newest first.

    Only SPOT carries rates — BB publishes none for forward or swap, so those
    rate fields are null rather than filled in from spot.
    """
    import datetime
    session = get_session()
    try:
        if days == 0:
            rows = session.execute(text(
                f"SELECT {_IBFX_COLS} FROM interbank_fx ORDER BY trade_date DESC, segment")).fetchall()
        else:
            since = datetime.date.today() - datetime.timedelta(days=days)
            rows = session.execute(text(
                f"SELECT {_IBFX_COLS} FROM interbank_fx WHERE trade_date >= :since "
                f"ORDER BY trade_date DESC, segment"), {"since": str(since)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/rates")
def get_fx_rates(days: int = Query(365, ge=0, le=3650), currency: str = Query(None)):
    """BB's published exchange rate of the Taka, one row per currency per day.

    `rate_date` is the trading day the rates DESCRIBE; `published_date` is when
    BB put them up — they differ by one business day and must not be conflated.
    """
    import datetime
    session = get_session()
    try:
        clauses, params = [], {}
        if days:
            clauses.append("rate_date >= :since")
            params["since"] = str(datetime.date.today() - datetime.timedelta(days=days))
        if currency:
            clauses.append("currency = :ccy")
            params["ccy"] = currency.upper()
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = session.execute(text(
            f"SELECT {_RATE_COLS} FROM fx_rates_daily{where} "
            f"ORDER BY rate_date DESC, currency"), params).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
