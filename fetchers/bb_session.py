"""
fetchers/bb_session.py — Shared F5/TSPD bypass utilities for BB pages.

All BB pages use F5/TSPD bot protection that fingerprints both the browser
JS environment and the TLS handshake (JA3/JA4). Selenium alone fails because:
  - Direct form POSTs from automated Chrome trigger CAPTCHA
  - Cookie replay via `requests` fails due to TLS fingerprint mismatch

Solution: Chrome clears the JS challenge and captures session cookies;
curl_cffi replays them with Chrome's exact TLS fingerprint.
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)


def get_chrome_version() -> Optional[int]:
    """Return Chrome's major version number from the Windows registry or filesystem."""
    import re as _re
    try:
        import winreg
        for hive, path in [
            (winreg.HKEY_CURRENT_USER,  r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
        ]:
            try:
                key = winreg.OpenKey(hive, path)
                ver, _ = winreg.QueryValueEx(key, "version")
                m = _re.match(r"(\d+)\.", str(ver))
                if m:
                    return int(m.group(1))
            except OSError:
                continue
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.check_output(
            ["powershell", "-c",
             r"(Get-Item 'C:\Program Files\Google\Chrome\Application\chrome.exe').VersionInfo.ProductVersion"],
            text=True, timeout=8, stderr=subprocess.DEVNULL,
        )
        m = _re.match(r"(\d+)\.", out.strip())
        if m:
            return int(m.group(1))
    except Exception:
        pass
    # Linux / macOS — parse `<chrome> --version` so undetected_chromedriver
    # downloads a driver matching the installed browser (critical in CI).
    try:
        import subprocess
        for binname in ("google-chrome", "google-chrome-stable",
                        "chromium-browser", "chromium", "chrome"):
            try:
                out = subprocess.check_output(
                    [binname, "--version"], text=True, timeout=8,
                    stderr=subprocess.DEVNULL,
                )
                m = _re.search(r"(\d+)\.", out)
                if m:
                    return int(m.group(1))
            except Exception:
                continue
    except Exception:
        pass
    return None


def _cleanup_chrome() -> None:
    """
    Kill stray Chrome/chromedriver processes left behind by a fetcher.

    A full refresh launches ~8 separate Chrome instances back-to-back; on the
    CI runner the leftovers from earlier launches starve later ones until Chrome
    fails to start at all. pkill frees those resources between fetchers.

    NO-OP on Windows — never kill the user's own Chrome on their local machine.
    """
    import sys
    if sys.platform == "win32":
        return
    try:
        import subprocess
        for pat in ("chromedriver", "chrome", "google-chrome"):
            subprocess.run(["pkill", "-9", "-f", pat],
                           capture_output=True, timeout=10)
    except Exception:
        pass


class F5Unavailable(RuntimeError):
    """The F5 challenge has stopped clearing for this process.

    Raised instead of launching yet another Chrome. From 23-Oct-2026 every
    hourly refresh was killed by its 30-minute timeout: Chrome started fine,
    the page never produced a table, and each page spent three attempts x ~95s
    waiting for it — four pages, ~20 minutes of a 30-minute budget. Seven
    consecutive runs.

    The cause turned out to be BB's own website serving a maintenance page
    (see fetchers/bb_health.py) — which is now detected directly and fails in
    seconds, as BBMaintenance. This breaker stays as the backstop for the
    OTHER way a page can fail to clear: a genuine F5 block, a challenge that
    changed, Chrome misbehaving. An earlier version of this docstring said BB
    was "refusing the runner"; that was a misreading of the maintenance page,
    and the breaker's rationale is only that retrying a page that will not
    clear is wasted time and wasted traffic, whatever the reason.
    """


# Per-process breaker. One page that exhausts its attempts means the challenge
# is not clearing; the remaining pages get ONE short probe (in case the block
# is path-specific, which BB's cookies sometimes are), and a second exhaustion
# means it is domain-wide and everything after fails instantly.
_F5_EXHAUSTED = 0
_F5_PROBE_WAIT = 35          # seconds, for the single probe after a failure
_F5_FULL_WAIT = 90


def _precheck(url: str) -> None:
    """Ask BB, in plain HTTP and in two seconds, whether it is down.

    Run BEFORE Chrome is launched. If BB is serving its maintenance page there
    is no challenge to clear and no table to wait for, and launching a browser
    to find that out cost ninety seconds per attempt. Any other outcome — a
    challenge page, a block, a network error — returns quietly and lets Chrome
    do its job: only BB's own maintenance words short-circuit anything.
    """
    from fetchers.bb_health import BBMaintenance, BBUnreachable, classify, unreachable_now
    try:
        from curl_cffi import requests as cf
        r = cf.get(url, impersonate="chrome", timeout=15)
        kind = classify(r.status_code, r.text)
    except Exception as exc:
        # A network error alone proves nothing, so by itself it lets Chrome try.
        # But when this run's own probes already found BB answering NOWHERE, a
        # second silence is not worth 3 x 120s of browser: fail in seconds.
        if unreachable_now():
            raise BBUnreachable(f"BB's website did not respond at {url} "
                                f"({type(exc).__name__}); every probe this run timed out") from exc
        return
    if kind == "maintenance":
        raise BBMaintenance(f"BB's website is serving its maintenance page at {url}")


def reset_f5_breaker() -> None:
    """For tests, and for a long-lived process that wants a fresh start."""
    global _F5_EXHAUSTED
    _F5_EXHAUSTED = 0


def get_f5_cookies(url: str, wait_selector: str = "table",
                   attempts: int = 3) -> tuple[dict, str]:
    """
    Open Chrome invisibly, load `url`, wait for the F5 JS challenge to clear
    (detected by `wait_selector` appearing), capture and return (cookies, user_agent).

    Retries the launch up to `attempts` times — Chrome occasionally fails to
    start in CI (resource pressure after many sequential launches). Stray
    processes are reaped after every attempt so the next one starts clean.

    Raises the last exception if all attempts fail — caller treats as empty.
    """
    import time
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    from fetchers.bb_health import BBMaintenance, looks_like_maintenance
    global _F5_EXHAUSTED
    _precheck(url)                      # BBMaintenance here costs ~2s, not ~5min
    if _F5_EXHAUSTED >= 2:
        raise F5Unavailable(
            "the F5 challenge did not clear on two separate pages in this run, so no "
            "further Chrome launches are being attempted — retrying a page that will "
            "not clear only wastes the run's time and adds traffic to BB")
    probe = _F5_EXHAUSTED >= 1
    if probe:
        attempts = 1                       # one look, not three
    wait_s = _F5_PROBE_WAIT if probe else _F5_FULL_WAIT

    last_exc: Optional[Exception] = None
    for i in range(attempts):
        options = uc.ChromeOptions()
        options.add_argument("--window-position=-10000,-10000")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        driver = None
        try:
            driver = uc.Chrome(options=options, version_main=get_chrome_version())
            driver.get(url)
            # Wait for the table OR for BB to say it is down, whichever comes
            # first. Waiting only for the table meant a maintenance page —
            # fully loaded in the first second — was waited on for ninety.
            WebDriverWait(driver, wait_s).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, wait_selector)
                or looks_like_maintenance(d.page_source)
            )
            if looks_like_maintenance(getattr(driver, "page_source", "")):
                raise BBMaintenance(f"BB's website is serving its maintenance page at {url}")
            time.sleep(5)
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
            ua = driver.execute_script("return navigator.userAgent")
            log.info("F5 cookies captured for %s (%d cookies)", url, len(cookies))
            return cookies, ua
        except BBMaintenance:
            raise                       # BB said it is down: no retry, no breaker count
        except Exception as exc:
            last_exc = exc
            log.warning("F5 cookie capture attempt %d/%d failed for %s: %s",
                        i + 1, attempts, url, exc)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            _cleanup_chrome()
        time.sleep(3)

    _F5_EXHAUSTED += 1
    log.error("F5 challenge did not clear for %s after %d attempt(s) — %d page(s) exhausted "
              "this run%s", url, attempts, _F5_EXHAUSTED,
              "; further Chrome launches are now disabled" if _F5_EXHAUSTED >= 2 else "")
    raise last_exc if last_exc else RuntimeError("get_f5_cookies: no attempts made")


def bb_post(url: str, data: dict, cookies: dict, ua: str) -> str:
    """
    POST `data` to `url` using curl_cffi Chrome TLS impersonation.
    Returns response text. Raises on HTTP error or CAPTCHA response.
    """
    from curl_cffi import requests as cf

    session = cf.Session(impersonate="chrome")
    session.headers.update({
        "User-Agent":      ua,
        "Referer":         url,
        "Origin":          "https://www.bb.org.bd",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control":   "no-cache",
    })
    for name, val in cookies.items():
        session.cookies.set(name, val, domain="www.bb.org.bd")

    resp = session.post(url, data=data, timeout=30)

    from fetchers.bb_health import BBMaintenance, looks_like_maintenance
    if looks_like_maintenance(resp.text):
        raise BBMaintenance(f"BB's website is serving its maintenance page at {url}")
    if "human visitor" in resp.text or "support ID" in resp.text:
        raise RuntimeError("F5 CAPTCHA returned — challenge may have rotated")

    return resp.text


def bb_get(url: str, cookies: dict, ua: str) -> str:
    """
    GET `url` using curl_cffi Chrome TLS impersonation + captured cookies.
    Returns response text. Raises on CAPTCHA response.
    """
    from curl_cffi import requests as cf

    session = cf.Session(impersonate="chrome")
    session.headers.update({
        "User-Agent":      ua,
        "Referer":         url,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    for name, val in cookies.items():
        session.cookies.set(name, val, domain="www.bb.org.bd")

    resp = session.get(url, timeout=30)

    from fetchers.bb_health import BBMaintenance, looks_like_maintenance
    if looks_like_maintenance(resp.text):
        raise BBMaintenance(f"BB's website is serving its maintenance page at {url}")
    if "human visitor" in resp.text or "support ID" in resp.text:
        raise RuntimeError("F5 CAPTCHA returned — challenge may have rotated")

    return resp.text


def bb_get_bytes(url: str, cookies: dict, ua: str) -> bytes:
    """Same as bb_get but returns the RAW BODY.

    bb_get returns resp.text, which decodes the body as text. That is right for
    HTML and destroys a PDF: the bytes are decoded, and re-encoding them cannot
    recover the original, so the file is corrupt and every parse fails for a
    reason that looks like a bad parser. Anything binary must come through here.
    """
    from curl_cffi import requests as cf

    session = cf.Session(impersonate="chrome")
    session.headers.update({
        "User-Agent":      ua,
        "Referer":         url,
        "Accept":          "application/pdf,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    for name, val in cookies.items():
        session.cookies.set(name, val, domain="www.bb.org.bd")

    resp = session.get(url, timeout=60)
    body = resp.content
    # The CAPTCHA comes back as HTML even when a PDF was asked for.
    if body[:200].lower().find(b"human visitor") != -1 or body[:200].lower().find(b"support id") != -1:
        raise RuntimeError("F5 CAPTCHA returned — challenge may have rotated")
    return body


def fetch_with_retry(fetch_once, attempts: int = 3, delay: int = 4, label: str = "fetch"):
    """
    Call `fetch_once()` (a full capture-cookies -> request -> parse cycle),
    retrying on any exception. Each retry re-runs the whole cycle, so a fresh
    F5 challenge is solved — the only reliable way past a rotated CAPTCHA.

    Returns whatever fetch_once returns. Returns [] if every attempt fails.
    """
    import time
    from fetchers.bb_health import BBMaintenance
    last_exc = None
    for i in range(attempts):
        try:
            return fetch_once()
        except BBMaintenance as exc:
            # BB said it is down. A retry re-runs the whole cookie-capture cycle
            # against a host that has just told us so - the reference-rate step
            # did exactly that three times in CI - so stop now, once, loudly.
            log.error("%s: %s - not retrying", label, exc)
            return []
        except Exception as exc:
            last_exc = exc
            log.warning("%s attempt %d/%d failed: %s", label, i + 1, attempts, exc)
            if i < attempts - 1:
                time.sleep(delay)
    log.error("%s: all %d attempts failed (last: %s)", label, attempts, last_exc)
    return []
