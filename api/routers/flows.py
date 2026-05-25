from fastapi import APIRouter, Query
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_flows(months: int = Query(6, ge=1, le=24)):
    """Daily net flows — coupon inflows, principal inflows, auction outflows."""
    import datetime
    since = datetime.date.today() - datetime.timedelta(days=months * 30)
    ahead = datetime.date.today() + datetime.timedelta(days=120)
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT flow_date, coupon_inflow_bdt_mill, principal_inflow_bdt_mill,
                   total_inflow_bdt_mill, auction_outflow_planned_mill,
                   auction_outflow_confirmed_mill, net_borrowing_bdt_mill,
                   coupon_payment_count, data_complete
            FROM daily_net_flow
            WHERE flow_date BETWEEN :since AND :ahead
            ORDER BY flow_date
        """), {"since": str(since), "ahead": str(ahead)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
