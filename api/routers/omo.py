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
    daily = []
    for d in p["days"]:
        row = {"Date": d["date"], "Day": d["weekday"]}
        for i in instruments:
            row[i] = d["by_instrument"].get(i)       # absent = blank, not 0
        row["Inflow (SDF maturing)"] = d["inflow_crore"]
        row["Outflow (injections maturing)"] = d["outflow_crore"]
        row["Net"] = d["net_crore"]
        row["Cumulative Net"] = d["cum_net_crore"]
        daily.append(row)

    tranches = [{
        "Maturity Date": d["date"], "Day": d["weekday"], "Instrument": i["instrument"],
        "Original Direction": i["direction"],
        "Effect at Maturity": "Inflow (cash to banks)" if i["liquidity_effect"] == "INFLOW"
                              else "Outflow (banks repay BB)",
        "Amount (crore)": i["crore"],
    } for d in p["days"] for i in d["items"]]

    totals = [{"Instrument": i, "Total (crore)": p["instrument_totals"][i]} for i in instruments]

    body = build_workbook(
        "Bangladesh Bank OMO Maturity Ladder",
        [("Daily ladder by product", daily), ("Tranche detail", tranches),
         ("Totals by instrument", totals)],
        facts=[("Window", f"{p['from']} to {p['to']}"), ("Unit", "BDT crore"),
               ("As of", p["as_of"]), ("OMO data from", p["omo_data_from"]),
               ("OMO maturities to", p["omo_data_to"]),
               ("Total inflow", p["total_inflow_crore"]),
               ("Total outflow", p["total_outflow_crore"]),
               ("Total net", p["total_net_crore"]),
               ("Source", "Bangladesh Bank OMO press releases")],
        caveats=[
            "An OMO's effect at maturity is the REVERSE of its original direction: an SDF "
            "(absorption) maturing returns cash to banks; a repo/AR/IBLF (injection) maturing "
            "takes cash out as the bank repays BB.",
            "Blank instrument cells mean that instrument had nothing maturing that day.",
            "Maturity-only lines published by BB (accepted = 0) are excluded, so the roll-off "
            "is not double-counted against the tranches they describe.",
            "Derived from live tranches. BB also prints its own maturity figures, and the "
            "weekly deep audit reconciles the two.",
        ],
    )
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{filename("bd_omo_maturity_ladder", p["from"], p["to"])}"'})
