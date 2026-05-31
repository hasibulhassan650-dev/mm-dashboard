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


def get_f5_cookies(url: str, wait_selector: str = "table") -> tuple[dict, str]:
    """
    Open Chrome invisibly, load `url`, wait for the F5 JS challenge to clear
    (detected by `wait_selector` appearing), capture and return (cookies, user_agent).

    Raises on failure — caller should catch and treat as empty result.
    """
    import time
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    options = uc.ChromeOptions()
    options.add_argument("--window-position=-10000,-10000")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    driver = uc.Chrome(options=options, version_main=get_chrome_version())
    try:
        driver.get(url)
        WebDriverWait(driver, 90).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
        )
        time.sleep(5)
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        ua = driver.execute_script("return navigator.userAgent")
        log.info("F5 cookies captured for %s (%d cookies)", url, len(cookies))
        return cookies, ua
    finally:
        try:
            driver.quit()
        except Exception:
            pass


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
