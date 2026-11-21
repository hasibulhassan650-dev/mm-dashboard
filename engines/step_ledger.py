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

# record_step_runs deletes rows older than this, so a failure streak that reaches this edge began at an unknown earlier time.
RETENTION_DAYS = 30
# EVERY step weekly_fetch runs writes a row each run it runs (ok when clean, failed/blocked/empty otherwise; weekly_fetch._settle),
# plus the Doctor's own heartbeat. That is what lets a failure be seen to END: a step that wrote a row only when it errored
# (remittance, reserves, ...) would hold the alert open for the whole retention window. A test fails if weekly_fetch runs a
# step that is not listed here, or lists one that never settles.
REPORTING_STEPS = DAILY_BB_STEPS | {"bgiib", "doctor", "self_heal", "pipeline", "secondary-gap-fill", "fx", "remittance",
                                    "reserves", "policy", "monetary", "sukuk", "holidays", "known_absences", "circulars", "backup"}
# step_health's window: a machine that has not reported inside it is not the one being judged.
PC_RECENT_HOURS = 14.0
# GitHub refuses an issue body over 65,536 characters; the bundle leaves room for the header around it.
BUNDLE_LIMIT = 60000
BODY_LIMIT = 65000


def step_of(error: str) -> str:
    """The step an error line belongs to ('interbank_repo: ...' -> 'interbank_repo'), or 'other'."""
    m = _STEP_PREFIX.match(str(error or ""))
    return m.group(1) if m else "other"


def build_rows(errors: Iterable[str], step_stats: Dict[str, Dict], run_utc: datetime.datetime,
               machine: str, step_errors: Optional[Dict[str, list]] = None) -> list:
    """The StepRun field dicts for one run. Pure: no database. Steps appear if they failed or reported what they did.

    `step_errors` is {step: the error lines that step's own block appended} (weekly_fetch._settle). A line is filed under the step
    its `<step>:` prefix names when that is a known step (a runner's line about another step stays with that step), else under the
    block that produced it: runners such as the holiday and sukuk fetchers prefix their lines with names of their own
    ('holiday_calendar:', none at all), which would otherwise land on a step that never writes an 'ok' and so never recovers.
    A line in `errors` that no block claimed falls back to its prefix, as before."""
    by_step: Dict[str, list] = {}
    unclaimed = [str(e) for e in errors or []]
    for block, lines in (step_errors or {}).items():
        for e in lines:
            e = str(e)
            if e in unclaimed:
                unclaimed.remove(e)
            p = step_of(e)
            by_step.setdefault(p if p in REPORTING_STEPS else block, []).append(e)
    for e in unclaimed:
        by_step.setdefault(step_of(e), []).append(e)
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
                     machine: Optional[str] = None, step_errors: Optional[Dict[str, list]] = None) -> int:
    """Persist build_rows(...). Returns how many rows were written. Never raises into the run.

    Volume: about 20 rows per run per machine, with hourly runs on the PC and a backup run on GitHub, so roughly 1,000 a day and
    30,000 inside the 30-day retention (a few MB; both columns the Doctor filters on are indexed). That is small enough that every
    run records every step: 'record only on change' would make an ok row mean 'unchanged', and a recovery could not be dated."""
    from db import StepRun
    if machine is None:
        from fetchers.news import machine as _machine
        machine = _machine()
    rows = build_rows(errors, step_stats, run_utc or datetime.datetime.utcnow(), machine, step_errors)
    for r in rows:
        session.add(StepRun(**r))
    # a month of history is plenty for the checks; the table must not grow for ever
    session.query(StepRun).filter(StepRun.run_utc < (run_utc or datetime.datetime.utcnow())
                                  - datetime.timedelta(days=RETENTION_DAYS)).delete()
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
    # The PC is the real reader and GitHub's runner is a backup that BB's firewall blocks as a matter of course: when the PC has
    # rows for this step in the window, the step is judged from the PC alone. A GitHub failure newer than the PC's ok must not
    # turn a healthy step red (the 16-Nov race, reversed). GitHub's rows stand in only when the PC has none.
    pc = [r for r in rows if r.machine != "github"]
    rows = pc if pc else rows
    oks = [r.run_utc for r in rows if r.status == "ok"]
    # GitHub's runner is blocked by BB's firewall as a matter of course (the PC is the real reader), so its empty/blocked reads
    # say nothing against a step; a FAILED step counts from any machine, and any non-ok status counts from the PC.
    bad = [r for r in rows if r.status == "failed" or (r.status in ("blocked", "empty") and r.machine != "github")]
    last_ok = max(oks) if oks else None
    last_bad = bad[-1] if bad else None
    healthy = last_ok is not None and not (last_bad is not None and last_bad.run_utc > last_ok)
    return True, healthy, last_bad


# ── Open failures: a failing step is an EPISODE, not a row ───────────────────────────────────────────────────────────────────

def current_streak(rows, now: Optional[datetime.datetime] = None) -> Optional[dict]:
    """The failure episode a step is in right now, from that ONE step's StepRun rows - or None when it is not failing.

    Pure: rows only need run_utc / machine / status / error_class / error / step. Judged like step_health, so the two cannot
    disagree about whether a step is healthy: while the PC (the real BB reader) has reported recently, only the PC's rows
    count; GitHub's rows stand in only when the PC has not. A GitHub 'blocked' or 'empty' read (BB's firewall does that to its
    runners as a matter of course) neither starts a streak nor breaks one. A run that is 'ok' ends it.

    `truncated` is True when the streak runs unbroken back to the retention edge: the real start is older than the ledger
    remembers, so a caller must say 'at least', never present the edge as the start.
    """
    rows = sorted(rows or [], key=lambda r: r.run_utc)
    if not rows:
        return None
    now = now or rows[-1].run_utc
    pc_reporting = any(r.machine != "github" and r.run_utc >= now - datetime.timedelta(hours=PC_RECENT_HOURS) for r in rows)
    judged = [r for r in rows if r.machine != "github"] if pc_reporting else rows
    run: list = []
    ended = False
    for r in judged:
        if r.status == "ok":
            run, ended = [], True
        elif r.machine == "github" and r.status in ("blocked", "empty"):
            continue
        else:
            run.append(r)
    if not run:
        return None
    first, last = run[0], run[-1]
    step = last.step
    cls = last.error_class or (F.EMPTY if last.status == "empty" else F.classify(last.error, step))
    truncated = (not ended) and first.run_utc <= now - datetime.timedelta(days=RETENTION_DAYS - 1)
    return {"step": step, "class": cls, "machine": last.machine, "first_failed_utc": first.run_utc,
            "last_failed_utc": last.run_utc, "attempts": len(run), "truncated": truncated, "last_error": last.error}


_SECRETS = re.compile(r"postgres(?:ql)?(?:\+\w+)?://\S+|sk-ant-[A-Za-z0-9_\-]+|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                      r"AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9_\-]{20,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]*)?", re.I)


def redact(text: Optional[str]) -> str:
    """The repo is PUBLIC and an issue body is public: a connection string (password included) or an API key must never reach one."""
    return _SECRETS.sub("[redacted]", str(text or ""))


def _excused(session, st: dict) -> bool:
    """A streak a RECORDED known absence explains is not an open failure: BB did not publish and we hold the evidence
    (data/seeds/known_absences.yaml). Only classes a missing publication can cause; a failure we could fix is never excused."""
    if st["class"] not in (F.EMPTY, F.PDF_UNSERVED):
        return False
    try:
        from engines.absences import absent_dates
        lo, hi = st["first_failed_utc"].date(), st["last_failed_utc"].date()
        return any(lo <= d <= hi for d in absent_dates(session, st["step"]))
    except Exception:
        return False                    # cannot prove it, so it stays open


def _tried(st: dict, pol: F.Policy, doctor_rows: list) -> list:
    """What the Doctor has done about this class, from its own heartbeat: the playbook, and whether it ran since this began."""
    out = [f"playbook '{pol.playbook}'" if pol.playbook else "no automatic repair exists for this class (diagnose only)"]
    since = [r for r in doctor_rows if r.run_utc >= st["first_failed_utc"]]
    if not since:
        out.append("the Doctor has not run since this began")
    else:
        d = since[-1]
        said = f"repaired {d.rows_stored or 0} item(s)" if d.status == "ok" else f"itself failed: {redact(d.error)[:200]}"
        out.append(f"the Doctor last ran {str(d.run_utc)[:16]} UTC on {d.machine or '?'} ({len(since)} run(s) since) and {said}")
    return out


def open_failures(session, now: Optional[datetime.datetime] = None) -> list:
    """Every reporting step that is failing right now, with its class, owner action and what the Doctor tried.

    Raises when step_runs cannot be read: the caller must treat that as 'unknown', never as 'nothing open'."""
    from db import StepRun
    now = now or datetime.datetime.utcnow()
    since = now - datetime.timedelta(days=RETENTION_DAYS)
    # Every step now writes an ok row every run (~30,000 rows a month). A step with no non-ok row in the window cannot be failing,
    # so only the others are loaded in full; the 'doctor' heartbeat is always needed for the 'what was tried' line.
    suspect = {r[0] for r in session.query(StepRun.step).filter(StepRun.run_utc >= since, StepRun.status != "ok").distinct()}
    rows = session.query(StepRun).filter(StepRun.run_utc >= since, StepRun.step.in_(suspect | {"doctor"})) \
        .order_by(StepRun.run_utc).all()
    by_step: Dict[str, list] = {}
    for r in rows:
        by_step.setdefault(r.step, []).append(r)
    out = []
    for step in sorted(by_step):
        # Rows under a name weekly_fetch never settles (legacy rows, a stray unclaimed error line) cannot be seen to recover.
        if step not in REPORTING_STEPS:
            continue
        st = current_streak(by_step[step], now)
        if st is None or _excused(session, st):
            continue
        pol = F.policy(st["class"])
        out.append({"step": step, "class": st["class"], "owner": pol.owner, "machine": st["machine"],
                    "first_failed_utc": str(st["first_failed_utc"]), "last_failed_utc": str(st["last_failed_utc"]),
                    "attempts": st["attempts"], "truncated": st["truncated"],
                    "owner_action": F.OWNER_ACTION.get(st["class"], F.OWNER_ACTION[F.UNKNOWN]),
                    "tried": _tried(st, pol, by_step.get("doctor", [])),
                    "last_error": redact(re.sub(r"\s+", " ", st["last_error"] or "")).strip()[:600]})
    return out


def render_bundle(failures: list, limit: int = BUNDLE_LIMIT) -> str:
    """The issue body for open failures. Pure. Secrets redacted, and cut to `limit` characters (GitHub rejects a body over 65,536)."""
    if not failures:
        return ""
    parts = [f"## Open failures ({len(failures)})",
             "Integrity checks may be clean, but these steps are still failing. This issue stays open until each has a clean "
             "run on the PC."]
    for f in failures:
        since = ("at least " if f.get("truncated") else "") + str(f.get("first_failed_utc", "?"))[:16] + " UTC"
        parts += ["", f"### {f.get('step')}: {f.get('class')} (since {since}; {f.get('attempts')} failed run(s); "
                      f"last on {f.get('machine') or '?'})",
                  f"- What to do: {f.get('owner_action')}",
                  f"- What the Doctor tried: " + "; ".join(f.get("tried") or ["nothing recorded"]),
                  f"- Last error: {str(f.get('last_error') or '(none recorded)').replace('`', chr(39))}"]
    text = redact("\n".join(parts))
    if len(text) > limit:
        text = text[:limit - 60].rstrip() + "\n\n[cut: this list is longer than a GitHub issue body allows]"
    return text


def may_close(report: dict) -> bool:
    """May the integrity alert issue be auto-closed? Only when the data checks pass AND the failure ledger was READ and holds
    nothing open. An unreadable ledger (open_failures is None) is not 'nothing open'."""
    of = (report or {}).get("open_failures")
    return (report or {}).get("ok") is True and isinstance(of, list) and not of
