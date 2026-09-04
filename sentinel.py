"""
sentinel.py — uptime + deploy-freshness watchdog.

Catches the two silent-failure classes this project has hit:
  1. a DEAD host (the Railway API once sat dead ~a week) — pings the live API and
     the live frontend and fails if either is unreachable;
  2. a STALE deploy (the drilldown once served old code) — compares the deployed
     frontend commit to the repo's HEAD and fails if it lags past a grace window.

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


def _http(url, timeout=25):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                       # noqa: BLE001 — any failure = unreachable
        return None, str(e)[:80]


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

    return {"ok": not problems, "problems": problems,
            "checked_utc": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}


if __name__ == "__main__":
    verdict = check()
    with open("verdict.json", "w", encoding="utf-8") as f:
        json.dump(verdict, f)
    print(json.dumps(verdict, indent=2))
    sys.exit(0 if verdict["ok"] else 1)
