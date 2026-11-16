"""Write one step_runs row per refresh step, per machine, per run - the ledger the Doctor and the freshness rule read.

Why a ledger when pipeline_runs already holds the errors: that table has ONE row per run with the errors as a JSON list of
free text, written alternately by the PC and by GitHub's runner, and the freshness rule judged every step from the single newest
row. On 12-16 Nov 2026 a blocked GitHub run (0 rows read, logged "OK") overwrote the PC's failing one, so a CheckViolation recorded
32 times was never seen. Here every machine's every step keeps its own row, with the class of failure and the counts.

A step is 'ok' only if it said it ran and read something: a daily BB step that fetched ZERO rows is 'empty' (never 'ok'), because
"succeeded at fetching nothing" is exactly the state that hid the incident.
"""
import datetime
import json
import re
from typing import Dict, Iterable, Optional

from engines import failures as F

# Daily BB-fed steps: reading zero rows from them on a working day is not success.
DAILY_BB_STEPS = {"callmoney", "refrate", "interbank_fx", "fxrates", "interbank_repo", "omo", "treasury"}
_STEP_PREFIX = re.compile(r"^([A-Za-z0-9_\-]+):")


def step_of(error: str) -> str:
    """The step an error line belongs to ('interbank_repo: ...' -> 'interbank_repo'), or 'other'."""
    m = _STEP_PREFIX.match(str(error or ""))
    return m.group(1) if m else "other"


def build_rows(errors: Iterable[str], step_stats: Dict[str, Dict], run_utc: datetime.datetime,
               machine: str) -> list:
    """The StepRun field dicts for one run. Pure: no database. Steps appear if they failed or reported what they did."""
    by_step: Dict[str, list] = {}
    for e in errors or []:
        by_step.setdefault(step_of(e), []).append(str(e))
    out = []
    for step in sorted(set(by_step) | set(step_stats or {})):
        errs = by_step.get(step, [])
        st = (step_stats or {}).get(step, {})
        fetched, stored, rejected = st.get("fetched"), st.get("stored"), st.get("rejected")
        if errs:
            text = " | ".join(errs)
            cls = F.classify(text, step)
            status = "blocked" if cls == F.BB_BLOCKED else "failed"
            error = text[:4000]
        elif step in DAILY_BB_STEPS and fetched == 0:
            status, cls, error = "empty", F.EMPTY, None
        else:
            status, cls, error = "ok", None, None
        out.append({"run_utc": run_utc, "machine": machine, "step": step, "status": status, "error_class": cls,
                    "rows_fetched": fetched, "rows_stored": stored, "rows_rejected": rejected, "error": error,
                    "evidence_json": json.dumps(st) if st else None})
    return out


def record_step_runs(session, errors, step_stats, run_utc: Optional[datetime.datetime] = None,
                     machine: Optional[str] = None) -> int:
    """Persist build_rows(...). Returns how many rows were written. Never raises into the run."""
    from db import StepRun
    if machine is None:
        from fetchers.news import machine as _machine
        machine = _machine()
    rows = build_rows(errors, step_stats, run_utc or datetime.datetime.utcnow(), machine)
    for r in rows:
        session.add(StepRun(**r))
    # a month of history is plenty for the checks; the table must not grow for ever
    session.query(StepRun).filter(StepRun.run_utc < (run_utc or datetime.datetime.utcnow())
                                  - datetime.timedelta(days=30)).delete()
    return len(rows)


def step_health(session, step: str, now: Optional[datetime.datetime] = None, max_age_hours: float = 14.0):
    """(known, healthy, last_failure) for a step, judged across BOTH machines.

    known    - the ledger holds at least one row for this step in the window (else the caller falls back to the old rule);
    healthy  - some machine's run is 'ok' inside the window AND no failed/blocked run of any machine is NEWER than the
               latest ok (a GitHub 'ok' cannot hide a PC failure that came after it, and an 'empty' read is never 'ok');
    last_failure - the newest failed/blocked/empty StepRun row inside the window, or None.
    """
    from db import StepRun
    now = now or datetime.datetime.utcnow()
    since = now - datetime.timedelta(hours=max_age_hours)
    rows = session.query(StepRun).filter(StepRun.step == step, StepRun.run_utc >= since).order_by(StepRun.run_utc).all()
    if not rows:
        return False, False, None
    oks = [r.run_utc for r in rows if r.status == "ok"]
    # GitHub's runner is blocked by BB's firewall as a matter of course (the PC is the real reader), so its empty/blocked reads
    # say nothing against a step; a FAILED step counts from any machine, and any non-ok status counts from the PC.
    bad = [r for r in rows if r.status == "failed" or (r.status in ("blocked", "empty") and r.machine != "github")]
    last_ok = max(oks) if oks else None
    last_bad = bad[-1] if bad else None
    healthy = last_ok is not None and not (last_bad is not None and last_bad.run_utc > last_ok)
    return True, healthy, last_bad
