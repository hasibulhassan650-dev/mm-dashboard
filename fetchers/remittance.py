"""
fetchers/remittance.py — Wage-earner remittance inflow (monthly) from BB econdata.

TARGET: https://www.bb.org.bd/en/index.php/econdata/wageremitance

F5/TSPD protected -> Chrome clears the challenge, curl_cffi GETs the page.
Table layout:
  Year/Month | Remittances
             | In million US dollar | In billion Taka
  2025-2026                                              <- fiscal-year header (1 cell)
  April      | 3127.30              | 383.85             <- month row (3 cells)
  ...
BD fiscal year runs Jul-Jun: in FY "2025-2026", Jul-Dec -> 2025, Jan-Jun -> 2026.
"""
import datetime
import logging
import re
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/econdata/wageremitance"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _f(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_remittance_html(html: str) -> List[Dict]:
    """Parse the monthly remittance table from page HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []

    out: List[Dict] = []
    fy_start: Optional[int] = None
    fy_end: Optional[int] = None

    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        # Fiscal-year header row, e.g. "2025-2026"
        fy = re.match(r"^(\d{4})\s*-\s*(\d{4})$", cells[0].strip())
        if len(cells) == 1 and fy:
            fy_start, fy_end = int(fy.group(1)), int(fy.group(2))
            continue
        # Month data row
        if len(cells) >= 3 and fy_start is not None:
            mname = cells[0].strip().lower()
            mnum = _MONTHS.get(mname)
            if mnum is None:
                continue
            year = fy_start if mnum >= 7 else fy_end
            usd = _f(cells[1])
            bdt = _f(cells[2])
            if usd is None and bdt is None:
                continue
            out.append({
                "month": f"{year:04d}-{mnum:02d}",
                "remittance_usd_mn": usd,
                "remittance_bdt_bn": bdt,
            })
    return out


def fetch_remittance() -> List[Dict]:
    """Fetch the full monthly remittance series via the F5 bypass."""
    from fetchers.bb_session import get_f5_cookies, bb_get
    try:
        cookies, ua = get_f5_cookies(_URL, wait_selector="table")
    except Exception as exc:
        log.error("Remittance: Chrome cookie capture failed: %s", exc)
        return []
    try:
        html = bb_get(_URL, cookies, ua)
    except Exception as exc:
        log.error("Remittance: GET failed: %s", exc)
        return []
    rows = parse_remittance_html(html)
    log.info("Remittance: parsed %d monthly rows", len(rows))
    return rows
