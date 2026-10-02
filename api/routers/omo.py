from fastapi import HTTPException, APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import text
from typing import Optional
import datetime
from db import get_session

router = APIRouter()

XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/transactions")
def get_transactions(
    days: int = Query(90, ge=1, le=365),
    instrument: Optional[str] = None,
):
    """Raw OMO transactions for the last N days."""
    since = datetime.date.today() - datetime.timedelta(days=days)
    session = get_session()
    try:
        q = """
            SELECT transaction_date, maturity_date, instrument, tenor_label,
                   tenor_days, accepted_bdt_crore, maturity_bdt_crore,
                   rate_pct, rate_range, direction, source_pdf
            FROM omo_transactions
            WHERE transaction_date >= :since
        """
        params = {"since": str(since)}
        if instrument:
            q += " AND instrument = :instr"
            params["instr"] = instrument.upper()
        q += " ORDER BY transaction_date DESC, instrument"
        rows = session.execute(text(q), params).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/outstanding")
def get_outstanding(days: int = Query(90, ge=7, le=365)):
    """
    Daily outstanding balance per instrument for the last N days.
    Returns: [{date, instrument, outstanding_bdt_crore}, ...]
    """
    today = datetime.date.today()
    since = today - datetime.timedelta(days=days)
    session = get_session()
    try:
        # Fetch all transactions that overlap the window
        rows = session.execute(text("""
            SELECT transaction_date, maturity_date, instrument, accepted_bdt_crore, direction
            FROM omo_transactions
            WHERE accepted_bdt_crore > 0
              AND maturity_date > :since
              AND transaction_date <= :today
            ORDER BY transaction_date
        """), {"since": str(since), "today": str(today)}).fetchall()

        txns = [dict(r._mapping) for r in rows]

        # Build daily series
        result = []
        d = since
        while d <= today:
            by_instr: dict = {}
            for t in txns:
                txn_date = t["transaction_date"]
                mat_date = t["maturity_date"]
                # normalise: could be string or date
                if isinstance(txn_date, str):
                    txn_date = datetime.date.fromisoformat(txn_date)
                if isinstance(mat_date, str):
                    mat_date = datetime.date.fromisoformat(mat_date)
                if txn_date <= d < mat_date:
                    key = (t["instrument"], t["direction"])
                    by_instr[key] = by_instr.get(key, 0.0) + (t["accepted_bdt_crore"] or 0.0)
            for (instr, direction), amt in by_instr.items():
                result.append({"date": str(d), "instrument": instr,
                               "direction": direction, "outstanding_bdt_crore": round(amt, 2)})
            d += datetime.timedelta(days=1)

        return result
    finally:
        session.close()


@router.get("/summary")
def get_summary():
    """Today's outstanding snapshot — one row per instrument."""
    today = datetime.date.today()
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT instrument, direction,
                   COUNT(*) as tranches,
                   ROUND(SUM(accepted_bdt_crore)::numeric, 2) as outstanding_bdt_crore,
                   MIN(maturity_date) as next_maturity,
                   MAX(maturity_date) as last_maturity
            FROM omo_transactions
            WHERE transaction_date <= :today AND maturity_date > :today
              AND accepted_bdt_crore > 0
            GROUP BY instrument, direction
            ORDER BY direction DESC, instrument
        """), {"today": str(today)}).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/maturity-ladder")
def get_maturity_ladder(
    days: int = Query(None, ge=1, le=400, description="Forward horizon from today"),
    date_from: str = Query(None, description="YYYY-MM-DD; may be in the past"),
    date_to: str = Query(None, description="YYYY-MM-DD"),
):
    """What rolls off on each day, by instrument, with the day's net and a
    running cumulative. BDT crore.

    /outstanding gives the daily STOCK per instrument; this gives the FLOW --
    which tranche matures when. Maturity-only rows (accepted = 0) are excluded
    so the roll-off is not double-booked.
    """
    from .liquidity_logic import omo_maturity_ladder
    session = get_session()
    try:
        return omo_maturity_ladder(session, days=days if days is not None else (
            None if (date_from or date_to) else 90), date_from=date_from, date_to=date_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        session.close()


@router.get("/maturity-ladder/export")
def export_maturity_ladder(days: int = Query(None, ge=1, le=400),
                           date_from: str = Query(None), date_to: str = Query(None)):
    """The OMO maturity ladder as a formatted workbook."""
    from .export_logic import build_workbook, filename
    from .liquidity_logic import omo_maturity_ladder
    session = get_session()
    try:
        p = omo_maturity_ladder(session, days=days if days is not None else (
            None if (date_from or date_to) else 90), date_from=date_from, date_to=date_to)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        session.close()

    instruments = p["instruments"]
    COVER = {"complete": "complete", "awaiting_publication": "not published yet",
             "future": "BB has not acted yet", "no_data": "no data"}
    daily = []
    for d in p["days"]:
        row = {"Date": d["date"], "Day": d["weekday"]}
        for i in instruments:
            row[f"Matured {i}"] = d["by_instrument"].get(i)    # absent = blank, not 0
        for i in instruments:
            row[f"Dealt {i}"] = d["new_by_instrument"].get(i)
        row["Maturing In"] = d["inflow_crore"]
        row["Maturing Out"] = d["outflow_crore"]
        row["Net Roll-off"] = d["roll_net_crore"]
        row["New Injection"] = d["new_inflow_crore"]
        row["New Absorption"] = d["new_outflow_crore"]
        row["Net OMO Flow"] = d["net_crore"]
        row["Cumulative Net Flow"] = d["cum_net_crore"]
        row["Coverage"] = COVER.get(d["omo_coverage"], d["omo_coverage"])
        daily.append(row)

    tranches = [{
        "Date": d["date"], "Day": d["weekday"], "Leg": leg,
        "Instrument": i["instrument"], "Original Direction": i["direction"],
        "Effect That Day": "Inflow (cash to banks)" if i["liquidity_effect"] == "INFLOW"
                           else "Outflow (cash to BB)",
        "Amount (crore)": i["crore"],
    } for d in p["days"]
      for leg, src in (("Dealt", d["new_items"]), ("Maturing", d["items"]))
      for i in src]

    totals = [{"Instrument": i,
               "Matured (crore)": p["instrument_totals"].get(i),
               "Dealt (crore)": p["new_instrument_totals"].get(i)} for i in instruments]

    body = build_workbook(
        "Bangladesh Bank OMO Maturity Ladder",
        [("Daily ladder by product", daily), ("Operation detail", tranches),
         ("Totals by instrument", totals)],
        facts=[("Window", f"{p['from']} to {p['to']}"), ("Unit", "BDT crore"),
               ("As of", p["as_of"]), ("OMO data from", p["omo_data_from"]),
               ("OMO operations published to", p["omo_dealt_to"]),
               ("OMO maturities known to", p["omo_data_to"]),
               ("Total maturing in", p["total_inflow_crore"]),
               ("Total maturing out", p["total_outflow_crore"]),
               ("Net roll-off", p["total_roll_net_crore"]),
               ("Total newly injected", p["total_new_inflow_crore"]),
               ("Total newly absorbed", p["total_new_outflow_crore"]),
               ("Net OMO flow", p["total_net_crore"]),
               ("Source", "Bangladesh Bank OMO press releases")],
        caveats=[
            "Every tranche moves liquidity TWICE, with opposite signs. On its DEAL date a new "
            "repo/AR/IBLF injects and a new SDF absorbs. At MATURITY the signs reverse: the "
            "repo is repaid so cash leaves, the SDF is returned so cash arrives.",
            "'Net Roll-off' is the maturity legs only — the funding cliff. 'Net OMO Flow' is "
            "all four legs — what actually happened to liquidity. They are different questions "
            "and only the second is a liquidity net.",
            f"Fresh operations are known only to {p['omo_dealt_to']}; after that the Coverage "
            "column says so and Net OMO Flow equals the roll-off, because BB's deals for those "
            "days are not published (or have not happened).",
            "Blank instrument cells mean that instrument had nothing on that leg that day.",
            "Maturity-only lines published by BB (accepted = 0) are excluded, so the roll-off "
            "is not double-counted against the tranches they describe.",
        ],
    )
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{filename("bd_omo_maturity_ladder", p["from"], p["to"])}"'})
