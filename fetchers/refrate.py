"""
fetchers/refrate.py — Fetch BB money market reference rates (DOMMR + BOFR).

TARGET: https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate

F5/TSPD bot protection blocks direct form POSTs even from automated Chrome.
Fix: Chrome loads the page to clear the JS challenge and capture session cookies,
then curl_cffi replays those cookies with Chrome's exact TLS fingerprint
(JA3/JA4 impersonation), making the POST indistinguishable from real Chrome.

TABLE STRUCTURE (two tables per page):
  Table 0 — DOMMR: date-header row, then rows of (Product, Amount, DOMMR%, Deals)
  Table 1 — BOFR:  date-header row, then rows of (Product, Amount, BOFR%,  Deals)
"""
import datetime
import logging
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate"
_TABLE_RATE_TYPE = {0: "DOMMR", 1: "BOFR"}


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_date_cell(s: str) -> Optional[datetime.date]:
    s = s.strip().rstrip(",").strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_refrate_html(html: str) -> List[Dict]:
    """Parse DOMMR+BOFR tables from any saved page HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    rows = []
    for tbl_idx, tbl in enumerate(tables):
        rate_type = _TABLE_RATE_TYPE.get(tbl_idx)
        if rate_type is None:
            continue
        current_date: Optional[datetime.date] = None
        for tr in tbl.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            if len(cells) == 1:
                parsed = _parse_date_cell(cells[0])
                if parsed:
                    current_date = parsed
                continue
            if len(cells) >= 4 and current_date is not None:
                rows.append({
                    "trade_date":    current_date,
                    "rate_type":     rate_type,
                    "product":       cells[0].strip(),
                    "amount_crore":  _parse_float(cells[1]),
                    "rate_pct":      _parse_float(cells[2]),
                    "num_deals":     _parse_int(cells[3]),
                })
    return rows


def _get_f5_cookies(url: str) -> tuple[dict, str]:
    """
    Open Chrome invisibly, let it clear the F5/TSPD JS challenge,
    return (cookies_dict, user_agent).
    """
    from fetchers.fx import _chrome_major_version
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import time

    options = uc.ChromeOptions()
    options.add_argument("--window-position=-10000,-10000")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options, version_main=_chrome_major_version())
    try:
        driver.get(url)
        WebDriverWait(driver, 90).until(
            EC.presence_of_element_located((By.TAG_NAME, "table"))
        )
        import time as _t; _t.sleep(5)
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        ua = driver.execute_script("return navigator.userAgent")
        return cookies, ua
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def fetch_refrate(days_back: int = 35) -> List[Dict]:
    """
    Fetch DOMMR + BOFR reference rates for the last `days_back` days.

    Uses Chrome to clear F5 challenge, then curl_cffi to POST the form
    with Chrome TLS impersonation (bypasses F5 fingerprinting).
    """
    try:
        from curl_cffi import requests as cf
    except ImportError:
        log.error("curl_cffi not installed: pip install curl_cffi")
        return []

    today = datetime.date.today()
    since = today - datetime.timedelta(days=days_back)
    range_str = f"{since.strftime('%d/%m/%Y')} - {today.strftime('%d/%m/%Y')}"

    log.info("RefRate: getting F5 session cookies via Chrome...")
    try:
        cookies, ua = _get_f5_cookies(_URL)
    except Exception as exc:
        log.error("RefRate: Chrome cookie capture failed: %s", exc)
        return []

    log.info("RefRate: POSTing with curl_cffi Chrome TLS impersonation (range=%s)...", range_str)
    try:
        session = cf.Session(impersonate="chrome")
        session.headers.update({
            "User-Agent":      ua,
            "Referer":         _URL,
            "Origin":          "https://www.bb.org.bd",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control":   "no-cache",
        })
        for name, val in cookies.items():
            session.cookies.set(name, val, domain="www.bb.org.bd")

        resp = session.post(_URL, data={"date_picker": range_str}, timeout=30)

        if "human visitor" in resp.text or "support ID" in resp.text:
            log.error("RefRate: CAPTCHA returned despite TLS impersonation (F5 may have rotated challenge)")
            return []

        rows = parse_refrate_html(resp.text)
        log.info("RefRate: parsed %d rows for %s", len(rows), range_str)
        return rows

    except Exception as exc:
        log.error("RefRate POST error: %s", exc)
        return []
