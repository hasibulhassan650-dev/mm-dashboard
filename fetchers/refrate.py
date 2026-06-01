"""
fetchers/refrate.py — Fetch BB money market reference rates (DOMMR + BOFR).

TARGET: https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate

Uses bb_session.get_f5_cookies + bb_session.bb_post to bypass F5/TSPD.
See fetchers/bb_session.py for the full explanation.

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


def fetch_refrate(days_back: int = 35) -> List[Dict]:
    """
    Fetch DOMMR + BOFR reference rates for the last `days_back` days.

    Uses Chrome to clear F5 JS challenge and capture session cookies,
    then curl_cffi POSTs the date-range form with Chrome TLS impersonation
    to bypass F5 fingerprinting.
    """
    from fetchers.bb_session import get_f5_cookies, bb_post, fetch_with_retry

    today = datetime.date.today()
    since = today - datetime.timedelta(days=days_back)
    range_str = f"{since.strftime('%d/%m/%Y')} - {today.strftime('%d/%m/%Y')}"
    log.info("RefRate: fetching %s", range_str)

    def _once():
        cookies, ua = get_f5_cookies(_URL)
        html = bb_post(_URL, {"date_picker": range_str}, cookies, ua)
        rows = parse_refrate_html(html)
        log.info("RefRate: parsed %d rows for %s", len(rows), range_str)
        return rows

    return fetch_with_retry(_once, label="RefRate")
