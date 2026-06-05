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
                   coupon_payment_count, data_complete,
                   inflow_security_count
            FROM daily_net_flow
            WHERE flow_date BETWEEN :since AND :ahead
            ORDER BY flow_date
        """), {"since": str(since), "ahead": str(ahead)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/drilldown")
def get_drilldown(date: str = Query(..., description="YYYY-MM-DD")):
    """All events (maturities, coupons, auctions) for a specific date."""
    session = get_session()
    try:
        maturities = session.execute(text("""
            SELECT m.isin, s.security_name_norm, s.security_type,
                   m.scheduled_date, m.payment_date, m.principal_bdt_mill, m.roll_days
            FROM maturity_events m
            LEFT JOIN securities s ON m.isin = s.isin
            WHERE m.scheduled_date = :date
            ORDER BY m.principal_bdt_mill DESC
        """), {"date": date}).fetchall()

        coupons = session.execute(text("""
            SELECT c.isin, s.security_name_norm, s.security_type,
                   c.scheduled_date, c.payment_date, c.amount_bdt_mill,
                   c.coupon_rate_used_pct, c.formula_string
            FROM coupon_events c
            LEFT JOIN securities s ON c.isin = s.isin
            WHERE c.scheduled_date = :date
            ORDER BY c.amount_bdt_mill DESC
        """), {"date": date}).fetchall()

        auctions = session.execute(text("""
            SELECT auction_date, settlement_date, security_type, tenor_label,
                   offered_amount_bdt_mill, accepted_amount_bdt_mill,
                   weighted_avg_yield_pct, outflow_status, roll_days, roll_reason
            FROM auction_events
            WHERE settlement_date = :date
            ORDER BY security_type, tenor_label
        """), {"date": date}).fetchall()

        mat_total  = sum(r.principal_bdt_mill or 0 for r in maturities)
        coup_total = sum(r.amount_bdt_mill    or 0 for r in coupons)
        auc_total  = sum((r.accepted_amount_bdt_mill or r.offered_amount_bdt_mill or 0) for r in auctions)

        return {
            "date": date,
            "summary": {
                "maturity_inflow_mill":  round(mat_total, 2),
                "coupon_inflow_mill":    round(coup_total, 2),
                "total_inflow_mill":     round(mat_total + coup_total, 2),
                "auction_outflow_mill":  round(auc_total, 2),
                "net_borrowing_mill":    round(auc_total - mat_total - coup_total, 2),
            },
            "maturities": [dict(r._mapping) for r in maturities],
            "coupons":    [dict(r._mapping) for r in coupons],
            "auctions":   [dict(r._mapping) for r in auctions],
        }
    finally:
        session.close()
