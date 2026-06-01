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
    from selenium.webdriver.support import expected_conditions as EC

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
            WebDriverWait(driver, 90).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
            )
            time.sleep(5)
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
            ua = driver.execute_script("return navigator.userAgent")
            log.info("F5 cookies captured for %s (%d cookies)", url, len(cookies))
            return cookies, ua
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

    if "human visitor" in resp.text or "support ID" in resp.text:
        raise RuntimeError("F5 CAPTCHA returned — challenge may have rotated")

    return resp.text


def fetch_with_retry(fetch_once, attempts: int = 3, delay: int = 4, label: str = "fetch"):
    """
    Call `fetch_once()` (a full capture-cookies -> request -> parse cycle),
    retrying on any exception. Each retry re-runs the whole cycle, so a fresh
    F5 challenge is solved — the only reliable way past a rotated CAPTCHA.

    Returns whatever fetch_once returns. Returns [] if every attempt fails.
    """
    import time
    last_exc = None
    for i in range(attempts):
        try:
            return fetch_once()
        except Exception as exc:
            last_exc = exc
            log.warning("%s attempt %d/%d failed: %s", label, i + 1, attempts, exc)
            if i < attempts - 1:
                time.sleep(delay)
    log.error("%s: all %d attempts failed (last: %s)", label, attempts, last_exc)
    return []
