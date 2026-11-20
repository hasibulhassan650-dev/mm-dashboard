"""
engines/guardian.py — the memory behind the integrity checks.

`integrity_check()` answers "what is wrong right now". It is deliberately pure:
it sits on read paths and must never write. What it cannot answer is "how long
has this been wrong", and that is the question that decides what to do:

    BB's auction calendar missing for a day  → they are late, wait
    BB's auction calendar missing for three weeks → they have stopped, act

So every run's verdict is folded into a ledger here. A problem seen on five
successive days is ONE row with a five-day age and five occurrences, not five
rows; when it stops appearing it is closed with the time it took to resolve.

Called from weekly_fetch.py (which already runs the check) and digest.py — never
from the API, so the read path stays side-effect free.
"""
import datetime
import logging
import re

from db import DataIssue, DataChange

log = logging.getLogger(__name__)

# Digits carry the date and the value, which change every day while the problem
# stays the same. Strip them so "2026-09-30 repo stale" and "2026-10-01 repo
# stale" are recognised as one continuing issue rather than two new ones.
_DIGITS = re.compile(r"\d+")


def fingerprint(text_: str) -> str:
    return _DIGITS.sub("#", text_.strip())[:80]


def _episodes(session, seen: dict, now: datetime.datetime) -> dict:
    """{fingerprint: streak} for the seen issues whose text names a failing step (failures.diagnosis_step) and whose step is in
    a failure episode now. Any trouble reading the ledger returns {} (the fingerprint age stands): ageing must never break the run."""
    from engines import failures as F
    from engines.step_ledger import RETENTION_DAYS, current_streak
    steps = {fp: F.diagnosis_step(line) for fp, (_sev, _table, line) in seen.items()}
    wanted = {s for s in steps.values() if s}
    if not wanted:
        return {}
    try:
        from db import StepRun
        rows = session.query(StepRun).filter(StepRun.step.in_(wanted),
                                             StepRun.run_utc >= now - datetime.timedelta(days=RETENTION_DAYS)).all()
    except Exception as exc:
        session.rollback()
        log.warning("Guardian: step_runs unreadable, issues keep their fingerprint age: %s", exc)
        return {}
    streaks = {s: current_streak([r for r in rows if r.step == s], now) for s in wanted}
    return {fp: streaks[s] for fp, s in steps.items() if s and streaks.get(s)}


def _since_note(ep: dict) -> str:
    """A streak that reaches the retention edge began at an unknown earlier time: say 'at least', never the edge as the start."""
    return f" (failing since at least {str(ep['first_failed_utc'])[:10]})" if ep.get("truncated") else ""


def record(session, report: dict, now: datetime.datetime | None = None) -> dict:
    """Fold one integrity report into the ledger. Returns what changed."""
    now = now or datetime.datetime.utcnow()
    seen: dict[str, tuple] = {}
    # Notices age in the ledger exactly like the rest: "BB reworded a header"
    # is only useful with a date on it, and it resolves itself automatically
    # once the new shape has held long enough to be BB's normal.
    for sev, key in (("defect", "issues"), ("waiting", "waiting"), ("notice", "notices")):
        for line in report.get(key) or []:
            table = line.split(":", 1)[0].strip()
            seen[fingerprint(line)] = (sev, table, line)

    # An issue that names the step behind it (validate appends the Doctor's diagnosis) is aged from that step's FAILURE EPISODE,
    # not from its fingerprint: a fingerprint outlives a recovery, so 'waiting 45d' was 45 days of the text recurring, not 45
    # days of this failure. Every other issue keeps the fingerprint's age.
    episode = _episodes(session, seen, now)
    existing = {r.fingerprint: r for r in session.query(DataIssue).all()}
    opened = reopened = closed = 0

    for fp, (sev, table, line) in seen.items():
        row = existing.get(fp)
        ep = episode.get(fp)
        if ep:
            line = line + _since_note(ep)
        if row is None:
            session.add(DataIssue(fingerprint=fp, table_name=table, severity=sev,
                                  message=line, first_seen=ep["first_failed_utc"] if ep else now, last_seen=now,
                                  occurrences=1))
            opened += 1
            continue
        if ep:
            row.first_seen = ep["first_failed_utc"]
        if row.resolved_at is not None:
            # It came back. Keep the original first_seen so the history shows a
            # recurring problem rather than a brand-new one each time.
            row.resolved_at = None
            row.resolution = None
            reopened += 1
        row.severity = sev                 # can flip: BB-late can become our bug
        row.message = line
        row.last_seen = now
        row.occurrences = (row.occurrences or 0) + 1

    for fp, row in existing.items():
        if fp not in seen and row.resolved_at is None:
            row.resolved_at = now
            lived = now - (row.first_seen or now)
            hours = lived.total_seconds() / 3600
            row.resolution = (f"cleared after {lived.days}d {int(hours % 24)}h "
                              f"({row.occurrences} sighting(s))")
            closed += 1

    session.flush()
    if opened or reopened or closed:
        log.info("Guardian ledger: %d opened, %d reopened, %d resolved", opened, reopened, closed)
    return {"opened": opened, "reopened": reopened, "resolved": closed, "open_now": len(seen)}


def record_repairs(session, healed: dict, now: datetime.datetime | None = None) -> int:
    """Persist what the self-heal changed, so automatic corrections are auditable."""
    now = now or datetime.datetime.utcnow()
    n = 0
    for line in (healed or {}).get("changes") or []:
        cls, _, detail = line.partition(":")
        session.add(DataChange(changed_utc=now, change_class=cls.strip()[:40],
                               detail=detail.strip() or line))
        n += 1
    session.flush()
    return n


def open_issues(session) -> list:
    """Everything still open, oldest first — defects before waiting."""
    rows = session.query(DataIssue).filter(DataIssue.resolved_at.is_(None)).all()
    now = datetime.datetime.utcnow()
    out = []
    for r in rows:
        age_h = (now - r.first_seen).total_seconds() / 3600 if r.first_seen else 0
        out.append({
            "fingerprint": r.fingerprint, "table": r.table_name, "severity": r.severity,
            "message": r.message, "first_seen": r.first_seen, "last_seen": r.last_seen,
            "occurrences": r.occurrences, "age_hours": round(age_h, 1),
            "age_days": int(age_h // 24),
        })
    out.sort(key=lambda x: (x["severity"] != "defect", -x["age_hours"]))
    return out
