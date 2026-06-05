from fastapi import APIRouter, Query
from sqlalchemy import text
from typing import Optional
import datetime
from db import get_session

router = APIRouter()


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
