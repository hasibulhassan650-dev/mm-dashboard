"""Is Bangladesh Bank's website telling us it is down?

From 22-Oct-2026 BB's site served this, in place of its pages:

    The Service is under Maintenance
    Our service is currently undergoing scheduled maintenance. We will be back
    online shortly.  Thank you for your patience.  Bangladesh Bank.

Every BB-fed series then stopped, and this system spent days blaming itself.
Chrome launched fine and waited ninety seconds per attempt for a table that was
never coming; each failed fetch was filed as "OUR fetch is not current"; the
freshness check escalated it as a fault in our own code; and when the plain
HTTP 403 body turned up while diagnosing, I mistook it for BB throttling this
machine. It was BB's own maintenance page, served to everyone. Two lessons, and
this module is the answer to both:

  * "BB is down" and "our fetch is broken" are different facts with different
    owners and different actions, and the only thing that separates them is
    what the response actually said. A bare 403 is not evidence — F5 returns
    one for a blocked client too — so detection matches BB's own words, not the
    status code.
  * The outage is PARTIAL and moves. On 24-Oct a GitHub runner read real pages
    over plain HTTP (interbank FX, repo, the holiday list) — all stale, the FX
    page still showing 18-Oct — while the F5-protected pages failed, and from a
    residential address every URL returned the maintenance page. So detection
    is per response, and the verdict is a spectrum, never one global flag.

The marker is deliberately narrow: BB's two phrases AND a small body. A real
page that happens to mention maintenance is tens of kilobytes; the maintenance
page is 759 bytes. Either alone would misfire.
"""
import datetime
import logging
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("bb_health")

# BB's own words. "Service Temporarily Unavailable" is only the <title> and is
# what a generic 503 from any proxy says, so it is NOT a marker on its own.
_MARKERS = ("scheduled maintenance", "under maintenance")

# The maintenance page is ~759 bytes. A genuine BB page that mentions the word
# "maintenance" (a press release, say) is far larger.
_MAX_BODY = 6000

PROBE_URLS: Tuple[str, ...] = (
    "https://www.bb.org.bd/",
    "https://www.bb.org.bd/en/index.php/mediaroom/press_release",
    "https://www.bb.org.bd/en/index.php/monetaryactivity/treasury",
)


# The most recent probe made BY THIS PROCESS. weekly_fetch runs the integrity
# check before it writes its run record, so a check reading only the database
# would always be one refresh behind its own probe — telling the desk BB was up
# in the very run that found it down.
LAST_PROBE: Optional[Dict] = None


class BBMaintenance(RuntimeError):
    """BB's site answered with its maintenance page.

    Raised instead of waiting for a table that is not coming. It is a fact
    about BB, not a failure of ours, and callers must not retry it: a retry is
    traffic to a host that has just said it is down.
    """


class BBUnreachable(BBMaintenance):
    """BB's site did not answer at all (every plain-HTTP probe timed out).

    A subclass, so every fail-fast path that already stops on BBMaintenance
    stops on this too - but it is NOT maintenance and must never be reported as
    such: from here we cannot tell BB being down from this runner being blocked,
    so it excuses nothing (the guardian still escalates). What it does is stop a
    run burning its whole time budget: on 25-Oct-2026 every Chrome step waited
    120s x 3 on a site that never answered, the 30-minute job limit cut the run
    short, and the steps after it never ran.
    """


def looks_like_maintenance(body: Optional[str]) -> bool:
    """True only for BB's maintenance page: its words, in a small document."""
    if not body or len(body) > _MAX_BODY:
        return False
    low = body.lower()
    return any(m in low for m in _MARKERS)


def check_response(resp) -> None:
    """Use in place of resp.raise_for_status() on any BB response.

    Identical for every ordinary failure, but when the body is BB's maintenance
    page it raises BBMaintenance - so the step's error in the run record says
    "BB's website is serving its maintenance page" instead of an anonymous
    "403 Client Error", which is how an outage got filed as a fault in our own
    fetch. Imported at each call site rather than wrapped around requests,
    because the fetchers use curl_cffi directly in a dozen places.
    """
    try:
        body = resp.text
    except Exception:
        body = None
    if looks_like_maintenance(body):
        raise BBMaintenance(f"BB's website is serving its maintenance page "
                            f"(HTTP {getattr(resp, 'status_code', '?')}) at "
                            f"{getattr(resp, 'url', 'a BB page')}")
    resp.raise_for_status()


def unreachable_now() -> bool:
    """True when THIS process's latest probe got no answer from any BB URL."""
    return bool(LAST_PROBE and LAST_PROBE.get("state") == "unreachable")


def classify(status: Optional[int], body: Optional[str]) -> str:
    """One response -> 'maintenance' | 'ok' | 'blocked' | 'unreachable'.

    'blocked' is a non-200 WITHOUT the marker: F5 refusing a client, a 5xx from
    something else. It must never be reported as maintenance — that would let
    our own block be blamed on BB.
    """
    if status is None:
        return "unreachable"
    if looks_like_maintenance(body):
        return "maintenance"
    if status == 200:
        return "ok"
    return "blocked"


def summarise(results: Sequence[Tuple[str, Optional[int], str]]) -> Dict:
    """Fold per-URL kinds into a state.

    maintenance - every URL we could read showed the maintenance page
    partial     - some did and some did not (the 24-Oct picture)
    up          - none did, and at least one was served normally
    unreachable - no URL answered at all (every probe timed out / refused)
    unknown     - nothing readable either way (only blocks, or a mix)

    'unknown' is a real answer. Treating "could not tell" as "up" would let an
    outage hide, and treating it as "down" would blame BB for our own network.
    """
    kinds = [k for _, _, k in results]
    n_maint = kinds.count("maintenance")
    n_ok = kinds.count("ok")
    if n_maint and not n_ok:
        state = "maintenance"
    elif n_maint and n_ok:
        state = "partial"
    elif n_ok:
        state = "up"
    elif kinds and all(k == "unreachable" for k in kinds):
        state = "unreachable"
    else:
        state = "unknown"
    return {"state": state,
            "probed_utc": datetime.datetime.utcnow().isoformat(timespec="seconds"),
            "detail": [{"url": u, "status": s, "kind": k} for u, s, k in results]}


def probe(urls: Sequence[str] = PROBE_URLS, timeout: int = 10) -> Dict:
    """Ask BB, in plain HTTP, whether it is serving its maintenance page.

    Three small GETs, no browser: this is meant to run first in every refresh
    and cost a couple of seconds, so that the expensive part (Chrome, ninety
    seconds per attempt) is never spent waiting on a site that has said it is
    down. A network error is 'unreachable', which is not maintenance.
    """
    from curl_cffi import requests as cr
    results: List[Tuple[str, Optional[int], str]] = []
    for u in urls:
        try:
            r = cr.get(u, impersonate="chrome", timeout=timeout)
            results.append((u, r.status_code, classify(r.status_code, r.text)))
        except Exception as exc:
            log.warning("probe %s failed: %s", u, type(exc).__name__)
            results.append((u, None, "unreachable"))
    out = summarise(results)
    global LAST_PROBE
    LAST_PROBE = out
    log.info("BB health: %s (%s)", out["state"],
             ", ".join(f"{k}" for _, _, k in results))
    return out
