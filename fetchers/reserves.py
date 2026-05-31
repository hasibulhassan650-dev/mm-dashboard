"""
fetchers/reserves.py — Foreign-exchange reserves (monthly) from BB econdata.

TARGET: https://www.bb.org.bd/en/index.php/econdata/intreserve

F5/TSPD protected -> Chrome clears the challenge, curl_cffi GETs the page.
Table layout (values in million US$):
  (In million US $)                                        <- units header (1 cell)
  Period | Foreign Exchange Reserves(Gross) | ...(BPM6)    <- column header
  2025-2026                                                <- fiscal-year header (1 cell)
  April  | 35111.2 | 30454.4                               <- month row (3 cells)
  ...
BD fiscal year runs Jul-Jun: in FY "2025-2026", Jul-Dec -> 2025, Jan-Jun -> 2026.
"""
import logging
import re
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/econdata/intreserve"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _f(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_reserves_html(html: str) -> List[Dict]:
    """Parse the monthly reserves table from page HTML."""
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
            gross = _f(cells[1])
            net = _f(cells[2])
            if gross is None and net is None:
                continue
            out.append({
                "month": f"{year:04d}-{mnum:02d}",
                "gross_reserves_usd_mn": gross,
                "net_reserves_bpm6_usd_mn": net,
            })
    return out


def fetch_reserves() -> List[Dict]:
    """Fetch the full monthly reserves series via the F5 bypass."""
    from fetchers.bb_session import get_f5_cookies, bb_get
    try:
        cookies, ua = get_f5_cookies(_URL, wait_selector="table")
    except Exception as exc:
        log.error("Reserves: Chrome cookie capture failed: %s", exc)
        return []
    try:
        html = bb_get(_URL, cookies, ua)
    except Exception as exc:
        log.error("Reserves: GET failed: %s", exc)
        return []
    rows = parse_reserves_html(html)
    log.info("Reserves: parsed %d monthly rows", len(rows))
    return rows
