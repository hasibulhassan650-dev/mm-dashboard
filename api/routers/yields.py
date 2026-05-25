from fastapi import APIRouter, Query
from sqlalchemy import text
from typing import Optional
from db import get_session

router = APIRouter()


@router.get("")
def get_yields(
    security_type: Optional[str] = None,
    tenor: Optional[str] = None,
    months: int = Query(12, ge=1, le=60),
):
    """Primary market yield history."""
    import datetime
    since = datetime.date.today().replace(day=1)
    # go back N months
    for _ in range(months - 1):
        since = (since - datetime.timedelta(days=1)).replace(day=1)

    session = get_session()
    try:
        q = """
            SELECT snapshot_date, auction_date, security_type, tenor_label,
                   tenor_years, cutoff_yield_pct, offered_bdt_crore, accepted_bdt_crore
            FROM primary_yield_snapshots
            WHERE auction_date >= :since
        """
        params: dict = {"since": str(since)}
        if security_type:
            q += " AND security_type = :stype"
            params["stype"] = security_type.upper()
        if tenor:
            q += " AND tenor_label = :tenor"
            params["tenor"] = tenor.upper()
        q += " ORDER BY auction_date DESC, tenor_years"
        rows = session.execute(text(q), params).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/curve")
def get_yield_curve():
    """Latest yield curve — most recent auction per tenor."""
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT DISTINCT ON (tenor_label)
                   tenor_label, tenor_years, security_type,
                   cutoff_yield_pct, auction_date
            FROM primary_yield_snapshots
            WHERE cutoff_yield_pct IS NOT NULL
            ORDER BY tenor_label, auction_date DESC
        """)).fetchall()
        return sorted([dict(r._mapping) for r in rows], key=lambda x: x["tenor_years"] or 0)
    finally:
        session.close()
