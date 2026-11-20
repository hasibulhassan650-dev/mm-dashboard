"""Per-dataset 'last run' for the Data Updates panel.

A dataset has three different times and the panel must not blur them:
  latest data   - the newest date BB has published that we hold (`latest_data`)
  last stored   - when a new row last arrived (`ingested`)
  last run      - when its refresh step last RAN, whether or not anything new was found (this module)
A quiet day (BB published nothing) moves only the last run, which is how 'checked and nothing new' is told
apart from 'not checked'.
"""
from sqlalchemy import text

# dataset key (api/routers/meta.py _SOURCES) -> the step_runs.step that refreshes it (weekly_fetch.note_step names)
STEP_OF = {
    "yields": "treasury",
    "omo": "omo",
    "callmoney": "callmoney",
    "fx": "fx",
    "refrate": "refrate",
    "secondary": "pipeline",
    "securities": "pipeline",
    "flows": "pipeline",
    "fxmarket": "interbank_fx",
    "fxrates": "fxrates",
    "repo": "interbank_repo",
}


def last_runs(session) -> dict:
    """{step: {run, status, machine, last_ok}} from the step ledger; {} when the ledger is unreadable."""
    try:
        latest = session.execute(text(
            "SELECT s.step, s.run_utc, s.status, s.machine FROM step_runs s "
            "JOIN (SELECT step, MAX(run_utc) AS m FROM step_runs GROUP BY step) x "
            "ON x.step = s.step AND x.m = s.run_utc")).fetchall()
        ok = dict(session.execute(text(
            "SELECT step, MAX(run_utc) FROM step_runs WHERE status = 'ok' GROUP BY step")).fetchall())
    except Exception:
        session.rollback()
        return {}
    out: dict = {}
    for step, run, status, machine in latest:
        out[step] = {"run": run.isoformat() if run is not None else None, "status": status, "machine": machine,
                     "last_ok": ok[step].isoformat() if ok.get(step) is not None else None}
    return out


def for_dataset(key: str, runs: dict) -> dict:
    """The dataset's last-run facts, or Nones when its step has never reported (shown as 'not recorded')."""
    r = runs.get(STEP_OF.get(key, ""), {})
    return {"last_run": r.get("run"), "last_run_status": r.get("status"), "last_run_machine": r.get("machine"),
            "last_ok_run": r.get("last_ok")}
