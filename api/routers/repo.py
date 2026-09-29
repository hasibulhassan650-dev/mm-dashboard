from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()

_COLS = """
    trade_date, num_deals, amount_crore,
    tenor_min_days, tenor_max_days,
    rate_min_pct, rate_max_pct, war_pct
"""


@router.get("")
def get_interbank_repo(days: int = Query(365, ge=0, le=3650)):
    """Interbank (secured) repo market, newest first.

    Read alongside call money: the repo-vs-call spread is the liquidity signal.
    BB's repo tenor is a 1–7 day RANGE, not overnight, so a small positive
    spread over overnight call is normal term premium, not a dislocation.
    """
    import datetime
    session = get_session()
    try:
        if days == 0:
            rows = session.execute(text(
                f"SELECT {_COLS} FROM interbank_repo ORDER BY trade_date DESC")).fetchall()
        else:
            since = datetime.date.today() - datetime.timedelta(days=days)
            rows = session.execute(text(
                f"SELECT {_COLS} FROM interbank_repo WHERE trade_date >= :since "
                f"ORDER BY trade_date DESC"), {"since": str(since)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
