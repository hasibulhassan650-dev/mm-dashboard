from fastapi import APIRouter
from sqlalchemy import text
from db import get_session

router = APIRouter()

# dataset key -> (table, timestamp column)
_SOURCES = {
    "securities": ("securities", "last_updated_utc"),
    "yields":     ("primary_yield_snapshots", "ingested_utc"),
    "secondary":  ("mtm_snapshots", "ingested_utc"),
    "omo":        ("omo_transactions", "ingested_utc"),
    "fx":         ("fx_auction_results", "ingested_utc"),
    "callmoney":  ("call_money_rates", "ingested_utc"),
    "refrate":    ("ref_rates", "ingested_utc"),
    "flows":      ("daily_net_flow", "computed_utc"),
}


@router.get("/freshness")
def get_freshness():
    """Max ingest/compute timestamp per dataset, so the UI can show 'updated X ago'."""
    session = get_session()
    out: dict = {}
    try:
        for key, (table, col) in _SOURCES.items():
            ts = session.execute(
                text(f"SELECT MAX({col}) FROM {table}")  # noqa: S608 — table/col from fixed map, not user input
            ).scalar()
            out[key] = ts.isoformat() if ts is not None else None
        return out
    finally:
        session.close()
