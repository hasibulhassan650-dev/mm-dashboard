"""watchdog.py - keep the data refresh running as often as it should.

GitHub's scheduler is a best effort, and a hostile one: the hourly refresh is
set for every hour and, on 25-Oct-2026, was delivered six times in 22 hours,
the last one landing in the middle of a BB outage. Nothing was wrong with the
cron line; scheduled workflows are simply delayed or dropped under load. So
this does not trust the cron. It runs on its own schedule (every ten minutes,
and being a different workflow it is throttled independently) and asks one
question: has the refresh run lately? If not, it starts one.

It decides from three facts, none of which needs the database:
  * how old the last refresh is  (the public /api/meta/status)
  * whether BB answers right now (a plain-HTTP probe, ~seconds)
  * whether a refresh is already running or was just started (gh)

When BB answers it refreshes as soon as the last run is older than
REFRESH_EVERY_MIN, so BB coming back is picked up within minutes rather than at
the next delivered cron. When BB is down it still runs, but rarely: a run
against a dead BB fails fast, costs little, and keeps the outage record and the
heartbeat current, but there is no point hammering a site that has said no.

Never starts a second run on top of one in flight, and never twice within
COOLDOWN_MIN - the two ways a watchdog turns into a flood.
"""
import datetime
import json
import subprocess
import sys
import urllib.request
from typing import Callable, Dict, Optional

API = "https://mm-dashboard-vac3.vercel.app"
WORKFLOW = "hourly_refresh.yml"

REFRESH_EVERY_MIN = 50          # BB up: refresh when the last run is older than this
BB_DOWN_EVERY_MIN = 180         # BB down/silent: still look, but rarely
COOLDOWN_MIN = 20               # never start two runs closer than this


def decide(age_min: Optional[float], bb_state: str, running: int,
           since_last_start_min: Optional[float]) -> Dict:
    """Pure decision: {'dispatch': bool, 'why': str}. Unit-tested."""
    if running:
        return {"dispatch": False, "why": f"{running} refresh run(s) already queued or in progress"}
    if since_last_start_min is not None and since_last_start_min < COOLDOWN_MIN:
        return {"dispatch": False,
                "why": f"a run was started {since_last_start_min:.0f} min ago (cooldown {COOLDOWN_MIN})"}
    up = bb_state in ("up", "partial")
    limit = REFRESH_EVERY_MIN if up else BB_DOWN_EVERY_MIN
    if age_min is None:
        # The status page could not tell us. If BB answers, refreshing is cheap
        # and may be exactly what is missing; otherwise do not guess.
        return {"dispatch": up, "why": "last refresh age unknown"
                + ("; BB answers, so refreshing" if up else "; BB does not answer, waiting")}
    if age_min >= limit:
        return {"dispatch": True,
                "why": f"last refresh {age_min:.0f} min ago, BB {bb_state} (limit {limit})"}
    return {"dispatch": False, "why": f"last refresh {age_min:.0f} min ago, within {limit}"}


def _age_minutes() -> Optional[float]:
    try:
        with urllib.request.urlopen(f"{API}/api/meta/status?_cb=watchdog", timeout=25) as r:
            h = json.loads(r.read().decode("utf-8", "replace")).get("run_age_hours")
        return None if h is None else float(h) * 60.0
    except Exception as exc:                       # noqa: BLE001 - any failure = unknown
        print(f"status unreadable: {type(exc).__name__}", file=sys.stderr)
        return None


def _gh_runs(runner: Callable = subprocess.run) -> list:
    r = runner(["gh", "run", "list", "--workflow", WORKFLOW, "--limit", "10",
                "--json", "status,createdAt"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "gh run list failed").strip()[:200])
    return json.loads(r.stdout or "[]")


def _summarise_runs(runs: list, now: datetime.datetime):
    running = sum(1 for x in runs if x.get("status") in ("queued", "in_progress", "waiting", "pending"))
    newest = None
    for x in runs:
        try:
            t = datetime.datetime.fromisoformat(x["createdAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        newest = t if newest is None or t > newest else newest
    since = None if newest is None else (now - newest).total_seconds() / 60.0
    return running, since


def main() -> int:
    from fetchers.bb_health import probe
    now = datetime.datetime.utcnow()
    try:
        bb = probe(timeout=15)["state"]
    except Exception as exc:                       # noqa: BLE001
        print(f"BB probe failed: {exc}", file=sys.stderr)
        bb = "unknown"
    try:
        running, since = _summarise_runs(_gh_runs(), now)
    except Exception as exc:                       # noqa: BLE001
        # Cannot see the queue: dispatching blind could double up. Fail loudly.
        print(f"cannot read the run queue: {exc}", file=sys.stderr)
        return 1
    verdict = decide(_age_minutes(), bb, running, since)
    print(json.dumps({"bb": bb, **verdict}))
    if verdict["dispatch"]:
        r = subprocess.run(["gh", "workflow", "run", WORKFLOW], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"dispatch failed: {(r.stderr or '').strip()[:200]}", file=sys.stderr)
            return 1
        print("dispatched", WORKFLOW)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
