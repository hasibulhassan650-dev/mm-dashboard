"""Is BB's website down right now, and since when?

fetchers/bb_health.py answers that for one moment. This answers it over time,
from what each refresh recorded in `pipeline_runs.quality["bb_status"]`, because
the question the desk and the guardian actually need is not "did one probe fail"
but "has BB been down since Thursday".

Why it exists: from 22-Oct-2026 BB's website served a maintenance page and every
BB-fed series stopped. The guardian then did the wrong thing for days, because
it had exactly two explanations for stale data — "BB has not published yet"
(waiting) and "our fetch is broken" (ours) — and no third one. BB being DOWN is
neither: nothing is wrong with the fetch, and nothing has failed to be
published. It needs its own wording and its own action (check BB, wait, tell the
desk the data is frozen), and it must flip back to "ours" the moment a probe says
BB is up and a fetch still fails.

It does NOT make anything quieter. A frozen feed is still frozen: the desk is
reading numbers N days old, and the severity still escalates on the dataset's
own tolerance. What changes is who is named and what is suggested.
"""
import datetime
import json
import logging
from typing import Dict, Iterable, List, Optional

log = logging.getLogger("bb_outage")

DOWN_STATES = ("maintenance", "partial")
# A probe older than this describes a world that may have changed, and "BB was
# down yesterday" must not excuse a dataset that is stale today.
FRESH_HOURS = 36


def _parse_ts(v) -> Optional[datetime.datetime]:
    if v is None or hasattr(v, "year"):
        return v
    try:
        return datetime.datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def status_of(quality) -> Optional[Dict]:
    """The bb_status block out of a pipeline_runs.quality JSON string or dict."""
    if quality is None:
        return None
    if isinstance(quality, str):
        try:
            quality = json.loads(quality)
        except Exception:
            return None
    st = quality.get("bb_status") if isinstance(quality, dict) else None
    return st if isinstance(st, dict) and st.get("state") else None


def outage_from_runs(runs: Iterable, now: datetime.datetime = None) -> Optional[Dict]:
    """Fold recent runs (newest first) into the current outage, or None.

    `runs` yields (run_utc, quality) pairs. The outage is the unbroken stretch of
    newest runs whose probe said maintenance/partial; `since` is the earliest
    probe in that stretch. A run with NO bb_status ends the walk, because
    nothing is known about it — and guessing "it was down" for runs that predate
    the probe would manufacture a start date nobody observed.
    """
    now = now or datetime.datetime.utcnow()
    stretch: List[Dict] = []
    first = True
    for run_utc, quality in runs:
        st = status_of(quality)
        if first:
            first = False
            ts = _parse_ts(run_utc)
            if st is None or ts is None or (now - ts).total_seconds() > FRESH_HOURS * 3600:
                return None
        if st is None or st["state"] not in DOWN_STATES:
            break
        stretch.append(st)
    if not stretch:
        return None
    newest, oldest = stretch[0], stretch[-1]
    return {"state": newest["state"], "probed_utc": newest.get("probed_utc"),
            "since": oldest.get("probed_utc"), "runs": len(stretch),
            "detail": newest.get("detail") or []}


def current(session, now: datetime.datetime = None) -> Optional[Dict]:
    """The outage in force now. Prefers this process's own probe, if it ran one,
    because weekly_fetch runs the integrity check BEFORE it writes its run
    record, and a verdict that could not see its own probe would be one run
    behind."""
    from sqlalchemy import text
    now = now or datetime.datetime.utcnow()
    runs: List[tuple] = []
    try:
        from fetchers import bb_health
        live = getattr(bb_health, "LAST_PROBE", None)
        if live and live.get("state"):
            runs.append((now, {"bb_status": live}))
    except Exception:
        pass
    try:
        rows = session.execute(text(
            "SELECT run_utc, quality FROM pipeline_runs ORDER BY run_utc DESC LIMIT 60")).fetchall()
        runs.extend((r[0], r[1]) for r in rows)
    except Exception:
        session.rollback()
    return outage_from_runs(runs, now)
