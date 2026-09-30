import datetime
from fastapi import APIRouter
from sqlalchemy import text
from db import get_session

router = APIRouter()

# Datasets BB publishes every working day → "current" means we hold the last
# working day's data. Event datasets (auctions / OMO operations) only exist on
# the days BB actually transacts, so "current" means the job has *checked*
# recently — there may simply be nothing newer to fetch.
# The refresh runs 3x/day, every day; missing all of them for this long means
# the automation is broken, not that Bangladesh Bank published nothing.
RUN_STALE_HOURS = 14

_DAILY_SERIES = {"callmoney", "refrate", "secondary", "flows",
                 "fxmarket", "fxrates", "repo"}
_EVENT_SERIES = {"yields", "omo", "fx"}      # auctions / OMO / FX interventions — not daily
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
    "fxmarket":   ("interbank_fx",           "ingested_utc",    "trade_date",      "Interbank FX"),
    "fxrates":    ("fx_rates_daily",         "ingested_utc",    "rate_date",       "Exchange Rates"),
    "repo":       ("interbank_repo",         "ingested_utc",    "trade_date",      "Interbank Repo"),
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


def _waiting_items(session) -> list:
    """Open "waiting on Bangladesh Bank" items, with how long we have waited.

    These are NOT defects in our data: BB simply has not published yet, so they
    never fail a build. They are surfaced anyway — with an age — because the
    difference between BB being a day late and BB having stopped publishing is
    exactly the age, and the consequence (a missing auction calendar means the
    cash-flow ladder has no outflow for those auctions) is real either way.
    """
    try:
        rows = session.execute(text(
            "SELECT table_name, message, first_seen, occurrences FROM data_issues "
            "WHERE resolved_at IS NULL AND severity = 'waiting' "
            "ORDER BY first_seen")).fetchall()
    except Exception:
        return []                       # ledger table not created yet
    out = []
    now = datetime.datetime.utcnow()
    for table_name, message, first_seen, occurrences in rows:
        age_h = _hours_since(first_seen)
        out.append({
            "table": table_name, "message": message,
            "since": first_seen.isoformat() if first_seen is not None else None,
            "age_days": int(age_h // 24) if age_h != float("inf") else None,
            "occurrences": occurrences,
        })
    return out


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

        # data_health is the verdict RECORDED BY THE LAST RUN — when the pipeline
        # dies it freezes, and the UI kept presenting that frozen opinion as
        # current (Sep-2026: a five-day-old "32 issues" banner while the real
        # state was "nothing has run at all"). Always ship its age so the page
        # can lead with the outage instead of the stale detail.
        run_age = _hours_since(last_run_dt)
        return {"datasets": datasets, "last_run": last_run, "last_run_errors": last_errors,
                "data_health": data_health, "cadence": "Auto-refreshed 3×/day",
                "checked_recently": checked_recently,
                "pipeline_alive": bool(run_age < RUN_STALE_HOURS),
                "run_age_hours": None if run_age == float("inf") else round(run_age, 1),
                "health_as_of": last_run,
                "health_stale": bool(data_health is not None and run_age >= RUN_STALE_HOURS),
                "waiting": _waiting_items(session),
                "as_of": str(today), "last_working_day": str(lwd)}
    finally:
        session.close()


@router.get("/guardian")
def get_guardian(limit: int = 40):
    """The issue ledger: what is open, what was resolved, what was auto-repaired.

    `integrity_check()` only ever answers "what is wrong now" and is overwritten
    each run. This is the memory behind it — every problem with a first-seen
    date, an age and how long it took to clear — because age is what separates
    "Bangladesh Bank is a day late" from "Bangladesh Bank has stopped
    publishing", and a fault that keeps coming back from one that happened once.
    """
    session = get_session()
    now = datetime.datetime.utcnow()
    try:
        def _rows(sql, **p):
            try:
                return session.execute(text(sql), p).fetchall()
            except Exception:
                return []          # ledger tables not created yet

        def _age(ts):
            h = _hours_since(ts)
            return None if h == float("inf") else round(h, 1)

        open_rows = []
        for r in _rows("SELECT fingerprint, table_name, severity, message, first_seen, "
                       "last_seen, occurrences FROM data_issues WHERE resolved_at IS NULL"):
            open_rows.append({
                "fingerprint": r[0], "table": r[1], "severity": r[2], "message": r[3],
                "first_seen": r[4].isoformat() if r[4] else None,
                "last_seen": r[5].isoformat() if r[5] else None,
                "occurrences": r[6], "age_hours": _age(r[4]),
            })
        # defects first, then longest-standing
        open_rows.sort(key=lambda x: (x["severity"] != "defect", -(x["age_hours"] or 0)))

        resolved = [{
            "table": r[0], "severity": r[1], "message": r[2],
            "first_seen": r[3].isoformat() if r[3] else None,
            "resolved_at": r[4].isoformat() if r[4] else None,
            "resolution": r[5], "occurrences": r[6],
        } for r in _rows(
            "SELECT table_name, severity, message, first_seen, resolved_at, resolution, occurrences "
            "FROM data_issues WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC LIMIT :n",
            n=limit)]

        repairs = [{
            "changed_utc": r[0].isoformat() if r[0] else None,
            "change_class": r[1], "detail": r[2],
        } for r in _rows("SELECT changed_utc, change_class, detail FROM data_changes "
                         "ORDER BY changed_utc DESC LIMIT :n", n=limit)]

        return {
            "as_of": now.isoformat(),
            "open": open_rows,
            "open_defects": sum(1 for r in open_rows if r["severity"] == "defect"),
            "open_waiting": sum(1 for r in open_rows if r["severity"] == "waiting"),
            "resolved": resolved,
            "repairs": repairs,
        }
    finally:
        session.close()
