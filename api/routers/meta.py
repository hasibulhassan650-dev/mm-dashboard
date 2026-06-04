from fastapi import APIRouter
from sqlalchemy import text
from db import get_session

router = APIRouter()

# dataset key -> (table, ingest-timestamp column, data-date column | None, label)
_SOURCES = {
    "yields":     ("primary_yield_snapshots", "ingested_utc",    "auction_date",    "Treasury Yields"),
    "omo":        ("omo_transactions",        "ingested_utc",    "transaction_date","OMO Operations"),
    "callmoney":  ("call_money_rates",        "ingested_utc",    "trade_date",      "Call Money"),
    "fx":         ("fx_auction_results",      "ingested_utc",    "auction_date",    "FX Auctions"),
    "refrate":    ("ref_rates",               "ingested_utc",    "trade_date",      "Reference Rates"),
    "secondary":  ("mtm_snapshots",           "ingested_utc",    "settlement_date", "Secondary (GSOM)"),
    "securities": ("securities",              "last_updated_utc", None,             "Securities Master"),
    "flows":      ("daily_net_flow",          "computed_utc",    "flow_date",       "Cash Flows"),
}


@router.get("/freshness")
def get_freshness():
    """Max ingest/compute timestamp per dataset (legacy shape: {key: iso})."""
    session = get_session()
    out: dict = {}
    try:
        for key, (table, col, _datecol, _label) in _SOURCES.items():
            ts = session.execute(text(f"SELECT MAX({col}) FROM {table}")).scalar()  # noqa: S608
            out[key] = ts.isoformat() if ts is not None else None
        return out
    finally:
        session.close()


@router.get("/status")
def get_status():
    """
    Rich freshness for the 'Updated' panel: per dataset the last ingest time,
    the latest data date, and row count — plus the last refresh-run time so the
    UI can honestly show 'auto-refreshed, last run X' rather than 'live'.
    """
    session = get_session()
    try:
        datasets = {}
        for key, (table, col, datecol, label) in _SOURCES.items():
            ing = session.execute(text(f"SELECT MAX({col}) FROM {table}")).scalar()  # noqa: S608
            n = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
            latest = None
            if datecol:
                d = session.execute(text(f"SELECT MAX({datecol}) FROM {table}")).scalar()  # noqa: S608
                latest = str(d) if d is not None else None
            datasets[key] = {
                "label": label,
                "ingested": ing.isoformat() if ing is not None else None,
                "latest_data": latest,
                "rows": n,
            }

        last_run = None
        last_errors = []
        data_health = None
        import json
        try:
            row = session.execute(text(
                "SELECT run_utc, errors, quality FROM pipeline_runs ORDER BY run_utc DESC LIMIT 1"
            )).fetchone()
            if row:
                last_run = row[0].isoformat() if row[0] is not None else None
                if row[1]:
                    try: last_errors = json.loads(row[1])
                    except Exception: last_errors = []
                if row[2]:
                    try: data_health = json.loads(row[2])
                    except Exception: data_health = None
        except Exception:
            # quality column or pipeline_runs may not exist yet
            try:
                row = session.execute(text("SELECT run_utc FROM pipeline_runs ORDER BY run_utc DESC LIMIT 1")).fetchone()
                if row:
                    last_run = row[0].isoformat() if row[0] is not None else None
            except Exception:
                pass

        return {"datasets": datasets, "last_run": last_run, "last_run_errors": last_errors,
                "data_health": data_health, "cadence": "Auto-refreshed 3×/day"}
    finally:
        session.close()
