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


PC_RECENT_HOURS = 14.0     # mirrors engines/step_ledger.PC_RECENT_HOURS (api/ cannot import engines/): the PC reads BB, GitHub is the backup


def current_problems(session, now=None) -> list:
    """What is wrong NOW, per step, judged from the PC when the PC has reported lately.

    BB's firewall CAPTCHAs GitHub's runners, so the newest run is often GitHub's and its 'errors' were the panel's headline
    ('policy fetch returned nothing; monetary: NoneType...') while the PC had read the same pages fine an hour earlier. A step
    is judged by the PC's newest row when it has one inside PC_RECENT_HOURS, else by the newest row of any machine. None
    when the ledger is unreadable (the caller then falls back to the pipeline run's own error list)."""
    import datetime
    now = now or datetime.datetime.utcnow()
    try:
        rows = session.execute(text(
            "SELECT run_utc, machine, step, status, error FROM step_runs WHERE run_utc > :s ORDER BY run_utc DESC"),
            {"s": now - datetime.timedelta(hours=36)}).fetchall()
    except Exception:
        session.rollback()
        return None
    pc_cut = now - datetime.timedelta(hours=PC_RECENT_HOURS)
    newest_any, newest_pc = {}, {}
    for run, machine, step, status, error in rows:
        run = run if isinstance(run, datetime.datetime) else datetime.datetime.fromisoformat(str(run)[:19])
        newest_any.setdefault(step, (run, machine, status, error))
        if machine == "pc" and run >= pc_cut:
            newest_pc.setdefault(step, (run, machine, status, error))
    out = []
    for step in sorted(newest_any):
        run, machine, status, error = newest_pc.get(step) or newest_any[step]
        if status != "ok":
            out.append(f"{step}: {status} ({machine}){' - ' + str(error).strip()[:80] if error else ''}")
    return out
