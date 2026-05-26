"""
fetchers/refrate.py — Fetch BB money market reference rates (DOMMR + BOFR).

TARGET: https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate

The default page load returns the latest day's data for both DOMMR and BOFR.
Form submission is blocked by F5/TSPD bot protection, so we scrape the raw page
daily and accumulate history in the DB.

TABLE STRUCTURE (two tables per page):
  Table 0 — DOMMR: date-header row, then rows of (Product, Amount, DOMMR%, Deals)
  Table 1 — BOFR:  date-header row, then rows of (Product, Amount, BOFR%,  Deals)

For bulk historical load, use parse_refrate_html() on a manually saved HTML file.
"""
import datetime
import logging
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate"

# Map table index → rate_type label
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
    """Parse date header cells like '24 May, 2026' or '24 May 2026'."""
    s = s.strip().rstrip(",").strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_refrate_html(html: str) -> List[Dict]:
    """Parse DOMMR+BOFR tables from any saved page HTML (raw or after-submit)."""
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

            # Date-header row: single cell containing a date like "24 May, 2026"
            if len(cells) == 1:
                parsed = _parse_date_cell(cells[0])
                if parsed:
                    current_date = parsed
                continue

            # Data row: Product, Amount, Rate%, Deals
            if len(cells) >= 4 and current_date is not None:
                rows.append({
                    "trade_date":   current_date,
                    "rate_type":    rate_type,
                    "product":      cells[0].strip(),
                    "amount_crore": _parse_float(cells[1]),
                    "rate_pct":     _parse_float(cells[2]),
                    "num_deals":    _parse_int(cells[3]),
                })

    return rows


def fetch_refrate() -> List[Dict]:
    """Load the raw reference rate page and return today's rows (no form needed)."""
    try:
        from fetchers.fx import _chrome_major_version
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
    except ImportError as e:
        log.warning("undetected-chromedriver not available: %s", e)
        return []

    import time

    options = uc.ChromeOptions()
    options.add_argument("--window-position=-10000,-10000")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=_chrome_major_version())
        driver.get(_URL)

        for _ in range(20):
            time.sleep(3)
            if driver.find_elements(By.TAG_NAME, "table"):
                break
        else:
            log.warning("RefRate: page never loaded table")
            return []

        rows = parse_refrate_html(driver.page_source)
        log.info("RefRate: parsed %d rows", len(rows))
        return rows

    except Exception as exc:
        log.error("RefRate fetch error: %s", exc)
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
