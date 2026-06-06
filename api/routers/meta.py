import datetime
from fastapi import APIRouter
from sqlalchemy import text
from db import get_session

router = APIRouter()

# Datasets BB publishes every working day → "current" means we hold the last
# working day's data. Event datasets (auctions / OMO operations) only exist on
# the days BB actually transacts, so "current" means the job has *checked*
# recently — there may simply be nothing newer to fetch.
_DAILY_SERIES = {"callmoney", "fx", "refrate", "secondary", "flows"}
_EVENT_SERIES = {"yields", "omo"}            # auctions / OMO ops — not daily
# (securities is master data → treated like an event series: fresh if checked)


def _last_working_day(today: datetime.date) -> datetime.date:
    """Most recent Bangladesh working day strictly before `today`.
    BD weekend = Friday (4) and Saturday (5)."""
    d = today - datetime.timedelta(days=1)
    while d.weekday() in (4, 5):
        d -= datetime.timedelta(days=1)
    return d


def _hours_since(ts) -> float:
    if ts is None:
        return float("inf")
    return (datetime.datetime.utcnow() - ts).total_seconds() / 3600.0


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
        # ── When did the refresh job last RUN (regardless of whether data changed)? ──
        last_run = None
        last_run_dt = None
        last_errors = []
        data_health = None
        import json
        try:
            row = session.execute(text(
                "SELECT run_utc, errors, quality FROM pipeline_runs ORDER BY run_utc DESC LIMIT 1"
            )).fetchone()
            if row:
                last_run_dt = row[0]
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
                    last_run_dt = row[0]
                    last_run = row[0].isoformat() if row[0] is not None else None
            except Exception:
                pass

        today = datetime.date.today()
        lwd = _last_working_day(today)
        checked_recently = _hours_since(last_run_dt) < 14   # ≥1 of the 3 daily runs landed

        # ── Per-dataset freshness, cadence-aware ──────────────────────────────
        datasets = {}
        for key, (table, col, datecol, label) in _SOURCES.items():
            ing = session.execute(text(f"SELECT MAX({col}) FROM {table}")).scalar()  # noqa: S608
            n = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
            latest = None
            latest_d = None
            if datecol:
                latest_d = session.execute(text(f"SELECT MAX({datecol}) FROM {table}")).scalar()  # noqa: S608
                latest = str(latest_d) if latest_d is not None else None

            # "current" = up to date for what BB has actually published.
            if key in _DAILY_SERIES:
                # daily series: current iff we hold at least the last working day
                current = latest_d is not None and latest_d >= lwd
            else:
                # event/master series: nothing is "due" daily — current iff the
                # job checked recently (so anything published would be captured)
                current = checked_recently

            datasets[key] = {
                "label": label,
                "ingested": ing.isoformat() if ing is not None else None,
                "latest_data": latest,
                "rows": n,
                "current": bool(current),
                "kind": "daily" if key in _DAILY_SERIES else "event",
            }

        return {"datasets": datasets, "last_run": last_run, "last_run_errors": last_errors,
                "data_health": data_health, "cadence": "Auto-refreshed 3×/day",
                "checked_recently": checked_recently,
                "as_of": str(today), "last_working_day": str(lwd)}
    finally:
        session.close()
