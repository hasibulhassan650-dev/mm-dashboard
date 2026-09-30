"""
sentinel.py — uptime, deploy-freshness AND data-liveness watchdog.

Catches the silent-failure classes this project has hit:
  1. a DEAD host (the Railway API once sat dead ~a week) — pings the live API and
     the live frontend and fails if either is unreachable;
  2. a STALE deploy (the drilldown once served old code) — compares the deployed
     frontend commit to the repo's HEAD and fails if it lags past a grace window;
  3. a DEAD PIPELINE behind a healthy site (Sep-2026: a rotated DATABASE_URL
     named a driver that was not installed, so every data job died at startup
     for FIVE DAYS while this sentinel reported green — it was watching the
     website, not the data);
  4. a FROZEN VERDICT: /api/meta/status serves the integrity result recorded by
     the last pipeline run, so when the pipeline dies the health banner keeps
     showing that run's opinion forever. An old verdict is itself an alarm.

This is deliberately the ONE watchdog with no database, no Chrome and no
Bangladesh Bank dependency — it reads the public API over HTTP — so it survives
exactly the failures that kill the pipeline.

Prints a JSON verdict and exits non-zero on any problem, so the workflow can
file/close a GitHub issue. Run locally with `python sentinel.py`.
"""
import datetime
import json
import subprocess
import sys
import urllib.error
import urllib.request

FRONTEND = "https://bbmarkets.vercel.app"
API      = "https://mm-dashboard-vac3.vercel.app"
DEPLOY_GRACE_MIN = 45   # time to allow Vercel to build+deploy a new commit
# The refresh runs 3x/day, every day. Missing ALL of them for 14h means the
# automation is broken, not that Bangladesh Bank published nothing.
RUN_STALE_HOURS = 14


def _http(url, timeout=25):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                       # noqa: BLE001 — any failure = unreachable
        return None, str(e)[:80]


def _hours_since_iso(ts):
    """Hours since an ISO timestamp from the API (naive = UTC). None if absent."""
    if not ts:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", ""))
    except ValueError:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (datetime.datetime.utcnow() - t).total_seconds() / 3600.0


def check():
    problems = []

    # 1) API alive — /api/meta/status is public and cheap
    st, _ = _http(f"{API}/api/meta/status?_cb=sentinel")
    if st != 200:
        problems.append(f"API unreachable: /api/meta/status -> {st}")

    # 2) Frontend alive — it's password-gated, so a 307 redirect to /login is a
    #    HEALTHY response; /login itself must render.
    st, _ = _http(f"{FRONTEND}/login")
    if st != 200:
        problems.append(f"Frontend unreachable: /login -> {st}")

    # 3) Deploy freshness — deploy-status is un-gated and exposes the commit SHA.
    st, body = _http(f"{FRONTEND}/api/deploy-status?_cb=sentinel")
    if st == 200 and body:
        try:
            deployed = (json.loads(body).get("commitSha") or "").strip().lower()
        except Exception:
            deployed = ""
        try:
            head = subprocess.check_output(["git", "rev-parse", "--short=7", "HEAD"]).decode().strip().lower()
            head_ts = int(subprocess.check_output(["git", "log", "-1", "--format=%ct"]).decode().strip())
            age_min = (datetime.datetime.now(datetime.timezone.utc).timestamp() - head_ts) / 60
        except Exception:
            head, age_min = "", 0
        if deployed and head and deployed != head and age_min > DEPLOY_GRACE_MIN:
            problems.append(f"Stale frontend deploy: serving {deployed}, main HEAD is {head} "
                            f"({int(age_min)} min old, past {DEPLOY_GRACE_MIN}-min grace)")
    # a non-200 here is not fatal on its own (older builds may still gate it)

    # 4) DATA liveness — the checks that would have caught the Sep-2026 outage.
    #    Everything here comes from the public status endpoint, so this keeps
    #    working when the database, Chrome or BB itself are unreachable.
    st, body = _http(f"{API}/api/meta/status?_cb=sentinel")
    if st != 200:
        problems.append(f"Data status unavailable: /api/meta/status -> {st}")
    else:
        try:
            s = json.loads(body)
        except Exception:
            s = None
            problems.append("Data status returned unparseable JSON")
        if s:
            age = _hours_since_iso(s.get("last_run"))
            if age is None:
                problems.append("No refresh run has ever been recorded (pipeline_runs is empty)")
            elif age > RUN_STALE_HOURS:
                problems.append(
                    f"REFRESH HAS NOT RUN for {age:.0f}h (last {s.get('last_run')}) — "
                    f"every figure on the site is at least that old. The site being up does "
                    f"NOT mean the data is current; check the Actions tab for failing jobs.")

            # Daily series must hold the last working day; the API works that out
            # itself (weekend/holiday aware), so trust its per-dataset verdict.
            #
            # But skip anything the integrity checks have already attributed to
            # Bangladesh Bank not having published: that is not a fault and not
            # actionable, and raising it here would re-open the same alarm the
            # severity split exists to quieten — the sentinel would just become
            # the new place the noise comes from.
            waiting_keys = set()
            for w in (s.get("waiting") or []):
                parts = str(w.get("message", "")).split(":")
                if len(parts) > 1:
                    waiting_keys.add(parts[1].strip())
            behind = [d.get("label", k) for k, d in (s.get("datasets") or {}).items()
                      if d.get("kind") == "daily" and not d.get("current") and k not in waiting_keys]
            if behind:
                problems.append("Daily series behind the last working day: " + ", ".join(sorted(behind)))

            health = s.get("data_health") or {}
            if health.get("ok") is False:
                by = health.get("by_table") or {}
                worst = ", ".join(f"{k}={v}" for k, v in sorted(by.items(), key=lambda x: -x[1])[:5])
                problems.append(f"Integrity checks failing: {health.get('issue_count')} issue(s) [{worst}]")
            # A verdict older than the run window is not a verdict about today.
            if age is not None and age > RUN_STALE_HOURS and health:
                problems.append(f"Integrity verdict is {age:.0f}h old — it describes stale data, not today's")

            last_errors = s.get("last_run_errors") or []
            if last_errors:
                problems.append(f"Last refresh reported {len(last_errors)} step error(s): "
                                + "; ".join(str(e)[:80] for e in last_errors[:3]))

    return {"ok": not problems, "problems": problems,
            "checked_utc": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}


if __name__ == "__main__":
    verdict = check()
    with open("verdict.json", "w", encoding="utf-8") as f:
        json.dump(verdict, f)
    print(json.dumps(verdict, indent=2))
    sys.exit(0 if verdict["ok"] else 1)
