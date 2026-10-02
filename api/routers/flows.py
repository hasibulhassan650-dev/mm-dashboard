from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from db import get_session

router = APIRouter()

XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
def get_forecast(
    days: int = Query(None, ge=1, le=400,
                      description="Forward horizon in days (legacy; ignored when a range is given)"),
    date_from: str = Query(None, description="YYYY-MM-DD; may be in the past"),
    date_to: str = Query(None, description="YYYY-MM-DD"),
):
    """
    Day-by-day known liquidity ladder for the banking system, in BDT crore.
    Only contracted, dated flows -- no modelling of future BB operations:
      + OMO absorption maturing (SDF)             -> cash returns to banks
      - OMO injection maturing (repo/AR/IBLF/...)  -> banks repay BB
      + G-sec coupon + maturity payments           -> govt pays banks
      - Auction settlements                        -> banks pay for new issuance

    Pass date_from/date_to to look at ANY window, including the past. `days`
    keeps working for callers that predate the range, and reproduces the old
    forward window exactly.
    """
    from .liquidity_logic import liquidity_ladder
    session = get_session()
    try:
        return liquidity_ladder(session, days=days, date_from=date_from, date_to=date_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
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
            WHERE m.payment_date = :date
            ORDER BY m.principal_bdt_mill DESC
        """), {"date": date}).fetchall()

        coupons = session.execute(text("""
            SELECT c.isin, s.security_name_norm, s.security_type,
                   c.scheduled_date, c.payment_date, c.amount_bdt_mill,
                   c.coupon_rate_used_pct, c.formula_string
            FROM coupon_events c
            LEFT JOIN securities s ON c.isin = s.isin
            WHERE c.payment_date = :date
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

        # Additive only: every *_mill field below predates this and is relied on
        # by the existing page, so OMO and the netted crore lines arrive beside
        # them rather than replacing anything.
        from .liquidity_logic import day_detail
        extra = day_detail(session, date)

        return {
            **extra,
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


@router.get("/by-product")
def get_by_product(years: int = Query(2, ge=1, le=20)):
    """Forward monthly schedule of redemptions, coupons and auction settlements,
    split T-Bond / T-Bill / FRTB, with Bangladesh FY subtotals. BDT crore."""
    from .schedule_logic import monthly_by_product
    session = get_session()
    try:
        return monthly_by_product(session, years)
    finally:
        session.close()


@router.get("/by-product/detail")
def get_by_product_detail(years: int = Query(2, ge=1, le=20)):
    """Every coupon and maturity behind /by-product — one row each, so any
    month's figure can be traced back to the securities paying it."""
    from .schedule_logic import event_detail
    session = get_session()
    try:
        return event_detail(session, years)
    finally:
        session.close()


# ── Formatted Excel exports ──────────────────────────────────────────────────
# Built server-side with openpyxl: bold frozen headers, number formats, sized
# columns, autofilter and a cover sheet. Unknown values are written as EMPTY
# cells, never 0 -- a zero in a spreadsheet is a number someone will sum.

@router.get("/by-product/export")
def export_by_product(years: int = Query(2, ge=1, le=20)):
    from .export_logic import build_workbook, filename
    from .schedule_logic import event_detail, monthly_by_product
    session = get_session()
    try:
        p = monthly_by_product(session, years)
        d = event_detail(session, years)
    finally:
        session.close()

    label = {"T_BOND": "T-Bond", "T_BILL": "T-Bill", "FRTB": "FRTB", "OTHER": "Other"}
    has_other = any(m["redemption"]["OTHER"] or m["coupon"]["OTHER"] or m["auction"]["OTHER"]
                    for m in p["months"])
    products = [x for x in p["products"] if x != "OTHER" or has_other]

    monthly, cum = [], 0.0
    for m in p["months"]:
        unknown = m["auction"]["status"] == "not_published"
        row = {"Month": m["month"], "Fiscal Year": m["fiscal_year"]}
        for k in products:
            row[f"Redemption {label[k]}"] = m["redemption"][k]
        row["Redemption Total"] = m["redemption"]["total"]
        for k in products:
            row[f"Coupon {label[k]}"] = m["coupon"][k] if k in p["coupon_products"] else None
        row["Coupon Total"] = m["coupon"]["total"]
        row["Inflow Total"] = m["inflow_total"]
        for k in products:
            row[f"Auction {label[k]}"] = None if unknown else m["auction"][k]
        row["Auction Total"] = None if unknown else m["auction"]["total"]
        row["Auction Data"] = "BB has not published" if unknown else m["auction"]["status"]
        row["Net Borrowing"] = None if unknown else m["net_borrowing"]
        if not unknown:
            cum += m["net_borrowing"]
        row["Cumulative Net Borrowing"] = None if unknown else round(cum, 2)
        monthly.append(row)

    fy = []
    for f in p["fy_subtotals"]:
        row = {"Fiscal Year": f["fiscal_year"], "Months": f["months"]}
        for k in products:
            row[f"Redemption {label[k]}"] = f["redemption"][k]
        row["Redemption Total"] = f["redemption"]["total"]
        row["Coupon Total"] = f["coupon"]["total"]
        row["Inflow Total"] = f["inflow_total"]
        row["Auction Total"] = f["auction"]["total"]
        row["Auction Months Published"] = f"{f['auction_months_published']} of {f['months']}"
        row["Net Borrowing"] = f["net_borrowing"] if f["net_borrowing_comparable"] else None
        fy.append(row)

    detail = [{
        "Kind": "Redemption (principal)" if r["kind"] == "REDEMPTION" else "Coupon",
        "Month": r["month"], "Payment Date": r["payment_date"],
        "Scheduled Date": r["scheduled_date"], "Product": label.get(r["product"], r["product"]),
        "ISIN": r["isin"], "Security": r["security"],
        "Coupon Rate %": r["coupon_rate_pct"],
        "Amount (crore)": r["amount_crore"], "Amount (mn)": r["amount_mill"],
    } for r in d["rows"]]

    body = build_workbook(
        f"Bangladesh G-Sec Schedule — {p['years']}-year horizon",
        [("Monthly by product", monthly), ("Fiscal year summary", fy),
         ("Every coupon & maturity", detail)],
        facts=[("Window", f"{p['from']} to {p['to']}"), ("Unit", "BDT crore"),
               ("Horizon", f"{p['years']} years"),
               ("Auction calendar published to", p["auction_calendar_to"]),
               ("Last month with scheduled flows", p["last_month_with_flows"]),
               ("Source", "Bangladesh Bank — auction calendar, treasury results, GSOM")],
        caveats=[
            "T-Bill coupon cells are blank because T-Bills are zero-coupon discount "
            "instruments. The return is the discount, and it appears in the redemption column.",
            f"Auction cells are BLANK after {p['auction_calendar_to']} because BB has not "
            "published a calendar that far ahead. Blank means unknown, NOT zero — do not "
            "read or sum it as no borrowing.",
            "Net borrowing is left blank wherever auction coverage is partial: netting full "
            "inflows against partial outflows would show a surplus that is an artefact of the "
            "missing calendar.",
            "Cash is dated to SETTLEMENT, not the contractual due date. The scheduled date is "
            "kept on every row of the detail sheet.",
        ],
        notes={"Every coupon & maturity":
               "One row per underlying event, so any figure on the monthly sheet can be traced "
               "back to the securities paying it."},
    )
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{filename("bd_gsec_schedule", str(p["years"]) + "y", p["from"], p["to"])}"'})


@router.get("/forecast/export")
def export_forecast(days: int = Query(None, ge=1, le=400),
                    date_from: str = Query(None), date_to: str = Query(None)):
    from .export_logic import build_workbook, filename
    from .liquidity_logic import liquidity_ladder
    session = get_session()
    try:
        p = liquidity_ladder(session, days=days, date_from=date_from, date_to=date_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        session.close()

    ladder = [{
        "Date": d["date"], "Day": d["weekday"],
        "OMO Return (SDF maturing)": d["omo_return_crore"] if d["omo_known"] else None,
        "OMO Repay (injections maturing)": d["omo_repay_crore"] if d["omo_known"] else None,
        "OMO Net": d["omo_net_crore"] if d["omo_known"] else None,
        "Govt Inflow (coupon + maturity)": d["govt_inflow_crore"],
        "Auction Outflow": d["auction_out_crore"],
        "Net": d["net_crore"], "Cumulative Net": d["cum_net_crore"],
        "OMO Data": "yes" if d["omo_known"] else "no data",
        "Flows Confirmed": "yes" if d["flows_confirmed"] else "no",
        "OMO Detail": " · ".join(
            f"{i['instrument']} {i['crore']:,.2f} ({'in' if i['liquidity_effect'] == 'INFLOW' else 'out'})"
            for i in d["omo_items"]) or None,
    } for d in p["days"]]

    items = [{
        "Date": d["date"], "Day": d["weekday"], "Instrument": i["instrument"],
        "Original Direction": i["direction"],
        "Effect at Maturity": "Inflow (cash to banks)" if i["liquidity_effect"] == "INFLOW"
                              else "Outflow (banks repay BB)",
        "Amount (crore)": i["crore"],
    } for d in p["days"] for i in d["omo_items"]]

    body = build_workbook(
        "Bangladesh Known Liquidity Ladder",
        [("Daily ladder", ladder), ("OMO maturities by product", items)],
        facts=[("Window", f"{p['from']} to {p['to']}"), ("Unit", "BDT crore"),
               ("As of", p["as_of"]), ("OMO data from", p["omo_data_from"]),
               ("OMO maturities to", p["omo_data_to"]),
               ("Auction calendar published to", p["auction_horizon"]),
               ("Source", "Bangladesh Bank — OMO press releases, auction calendar, GSOM")],
        caveats=[
            "Only contracted, dated flows. Future BB operations are NOT modelled — they react "
            "to this ladder, and the gap between the two is where the rate moves.",
            "An OMO's effect at maturity is the REVERSE of its original direction: an SDF "
            "(absorption) maturing pays cash back to banks, while a repo (injection) maturing "
            "takes cash out.",
            f"OMO columns are BLANK before {p['omo_data_from']} because there is no OMO data "
            "that far back. Blank means unknown, not that BB ran no operations.",
            f"Auction outflows after {p['auction_horizon']} are missing from Net, because BB "
            "has not published the calendar. Net on those days overstates liquidity.",
        ],
    )
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{filename("bd_liquidity_ladder", p["from"], p["to"])}"'})
