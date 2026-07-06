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


@router.get("/forecast")
def get_forecast(days: int = Query(21, ge=7, le=60)):
    """
    Forward daily liquidity ladder for the banking system, in BDT crore.
    Combines the flows that are KNOWN and dated today (no modelling of
    future BB operations):
      + OMO absorption maturing (SDF)          -> cash returns to banks
      - OMO injection maturing (repo/AR/IBLF/SLF) -> banks repay BB
      + G-sec coupon + maturity payments        -> govt pays banks
      - Auction settlements                     -> banks pay for new issuance
    """
    import datetime
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days)
    session = get_session()
    try:
        omo = session.execute(text("""
            SELECT maturity_date, instrument, direction,
                   SUM(accepted_bdt_crore) AS amt_crore
            FROM omo_transactions
            WHERE transaction_date <= :today
              AND maturity_date > :today AND maturity_date <= :end
              AND accepted_bdt_crore > 0
            GROUP BY maturity_date, instrument, direction
            ORDER BY maturity_date
        """), {"today": str(today), "end": str(end)}).fetchall()

        flows = session.execute(text("""
            SELECT flow_date, coupon_inflow_bdt_mill, principal_inflow_bdt_mill,
                   auction_outflow_confirmed_mill, auction_outflow_planned_mill,
                   data_complete
            FROM daily_net_flow
            WHERE flow_date > :today AND flow_date <= :end
            ORDER BY flow_date
        """), {"today": str(today), "end": str(end)}).fetchall()

        # Honesty guard: auction outflows beyond the last known auction event
        # are UNKNOWN (calendar not ingested), not zero. Expose the horizon so
        # the UI can say so instead of implying a flush month.
        auction_horizon = session.execute(text(
            "SELECT MAX(settlement_date) FROM auction_events"
        )).scalar()

        omo_by_day: dict = {}
        for r in omo:
            omo_by_day.setdefault(str(r.maturity_date), []).append({
                "instrument": r.instrument,
                "direction":  r.direction,
                "crore":      round(r.amt_crore, 2),
            })
        flow_by_day = {str(r.flow_date): r for r in flows}

        out = []
        cum = 0.0
        d = today + datetime.timedelta(days=1)
        while d <= end:
            key = str(d)
            items = omo_by_day.get(key, [])
            omo_return = sum(i["crore"] for i in items if i["direction"] == "ABSORPTION")
            omo_repay  = sum(i["crore"] for i in items if i["direction"] == "INJECTION")
            f = flow_by_day.get(key)
            govt_inflow = ((f.coupon_inflow_bdt_mill or 0) + (f.principal_inflow_bdt_mill or 0)) / 10.0 if f else 0.0
            auction_out = ((f.auction_outflow_confirmed_mill or f.auction_outflow_planned_mill or 0) / 10.0) if f else 0.0
            net = omo_return - omo_repay + govt_inflow - auction_out
            cum += net
            out.append({
                "date":               key,
                "weekday":            d.strftime("%a"),
                "omo_return_crore":   round(omo_return, 2),
                "omo_repay_crore":    round(omo_repay, 2),
                "govt_inflow_crore":  round(govt_inflow, 2),
                "auction_out_crore":  round(auction_out, 2),
                "net_crore":          round(net, 2),
                "cum_net_crore":      round(cum, 2),
                "omo_items":          items,
                "flows_confirmed":    bool(f.data_complete) if f is not None else False,
            })
            d += datetime.timedelta(days=1)

        return {"as_of": str(today), "days": out, "unit": "BDT crore",
                "auction_horizon": str(auction_horizon) if auction_horizon else None}
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
