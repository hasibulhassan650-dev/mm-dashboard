"""
fetchers/callmoney.py — Fetch Call Money Market rates from BB.

TARGET: https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market

PAGE STRUCTURE:
  Form field: date_picker (type=text), value format: "26/04/2026 - 26/05/2026"
  Table columns (7 data cells per row, date header rows interspersed):
    Product | Maturity | Amount(Crore Taka) | Highest% | Lowest% | Average% | Num Deals

  Date header rows contain a single cell like "24 May, 2026".
  Data rows have 7 cells; the table header has 8 (Interest rate% spans Highest/Lowest/Average).

COLUMN MAPPING (idx → field):
  0 → product        (Overnight / Short Notice / Term)
  1 → maturity_str   (e.g. "1 Day(/s)", "14 Day(/s)")
  2 → amount_crore
  3 → highest_rate_pct
  4 → lowest_rate_pct
  5 → average_rate_pct
  6 → num_deals
"""
import datetime
import logging
import re
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market"


def _parse_maturity_days(s: str) -> Optional[int]:
    """'1 Day(/s)' → 1, '14 Day(/s)' → 14, '3 Month(/s)' → 90"""
    s = s.strip()
    m = re.match(r"^(\d+)\s*day", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)\s*month", s, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 30
    return None


def _parse_date(s: str) -> Optional[datetime.date]:
    for fmt in ("%d %B, %Y", "%d %b, %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(float(str(s).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _parse_table(html: str) -> List[Dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    rows = []
    current_date: Optional[datetime.date] = None

    for tr in tables[0].find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue

        # Date header row: single non-empty cell that parses as a date
        if len(cells) == 1:
            d = _parse_date(cells[0])
            if d:
                current_date = d
            continue

        # Data row: 7 cells
        if len(cells) >= 7 and current_date:
            product      = cells[0].strip()
            mat_str      = cells[1].strip()
            amount       = _parse_float(cells[2])
            highest      = _parse_float(cells[3])
            lowest       = _parse_float(cells[4])
            average      = _parse_float(cells[5])
            num_deals    = _parse_int(cells[6])
            mat_days     = _parse_maturity_days(mat_str)

            if not product or amount is None:
                continue

            rows.append({
                "trade_date":       current_date,
                "product":          product,
                "maturity_days":    mat_days,
                "amount_crore":     amount,
                "highest_rate_pct": highest,
                "lowest_rate_pct":  lowest,
                "average_rate_pct": average,
                "num_deals":        num_deals,
            })

    return rows


def fetch_call_money(days_back: int = 30) -> List[Dict]:
    """
    Fetch BB call money market rates for the last `days_back` days.

    Uses Chrome to clear F5 JS challenge and capture session cookies,
    then curl_cffi POSTs the date-range form with Chrome TLS impersonation
    to bypass F5 fingerprinting.
    """
    from fetchers.bb_session import get_f5_cookies, bb_post

    today     = datetime.date.today()
    date_from = today - datetime.timedelta(days=days_back)
    range_str = f"{date_from.strftime('%d/%m/%Y')} - {today.strftime('%d/%m/%Y')}"
    log.info("Call money: fetching %s", range_str)

    try:
        cookies, ua = get_f5_cookies(_URL, wait_selector="input[name='date_picker']")
    except Exception as exc:
        log.error("Call money: Chrome cookie capture failed: %s", exc)
        return []

    try:
        html = bb_post(_URL, {"date_picker": range_str}, cookies, ua)
    except Exception as exc:
        log.error("Call money: POST failed: %s", exc)
        return []

    rows = _parse_table(html)
    log.info("Call money: parsed %d rows", len(rows))
    return rows
