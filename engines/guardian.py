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


def record(session, report: dict, now: datetime.datetime | None = None) -> dict:
    """Fold one integrity report into the ledger. Returns what changed."""
    now = now or datetime.datetime.utcnow()
    seen: dict[str, tuple] = {}
    for sev, key in (("defect", "issues"), ("waiting", "waiting")):
        for line in report.get(key) or []:
            table = line.split(":", 1)[0].strip()
            seen[fingerprint(line)] = (sev, table, line)

    existing = {r.fingerprint: r for r in session.query(DataIssue).all()}
    opened = reopened = closed = 0

    for fp, (sev, table, line) in seen.items():
        row = existing.get(fp)
        if row is None:
            session.add(DataIssue(fingerprint=fp, table_name=table, severity=sev,
                                  message=line, first_seen=now, last_seen=now,
                                  occurrences=1))
            opened += 1
            continue
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
