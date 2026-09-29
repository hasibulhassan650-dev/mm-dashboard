"""
fetchers/fxrates.py — BB's daily exchange rate of the Taka, all currencies.

TARGET: https://www.bb.org.bd/en/index.php/econdata/exchangerate

This is the only BB page carrying EUR, GBP, JPY and the rest — the interbank
FX market page is USD-only. Two tables:
    A. USD/BDT    : Currency | Bid Rate | Ask Rate | WAR
    B. Cross Rates: Currency | Bid Rate | Ask Rate      (EUR GBP AUD JPY CAD
                                                         SEK SGD CNH INR LKR)
The mid-rate is (bid + ask) / 2, computed at read time — a stored derived
column drifts away from the inputs it came from.

*** THE DATE TRAP — read before touching this. ***
The page heads itself "Exchange Rate of Taka (Sep 29, 2026)" but its own note
says the USD bid/ask "represent the highest and lowest interbank exchange rates
at Dhaka close on the PREVIOUS BUSINESS DAY", with cross rates on that same
day. So the headline date is when BB PUBLISHED, not the day the rates describe.
Storing the headline date as the rate date would put every FX rate one business
day late — the identical mistake the treasury page caused by printing the issue
date where we assumed the auction date.

Verified numerically: the page published 29-Sep-2026 shows USD bid 122.7500 /
ask 122.8500 / WAR 122.8100, which is exactly the interbank SPOT row for
28-Sep (low 122.75, high 122.85, WAR 122.81). Bid = the day's low, ask = the
day's high. So `rate_date` = previous working day of `published_date`, and both
are stored — one to analyse on, one to trace back to the page it came from.

Like fetchers/monetary.py this page passes on TLS impersonation alone; no
Chrome/F5 hop needed.
"""
import datetime
import logging
import re
from typing import List, Dict, Optional

import calendar_utils

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/econdata/exchangerate"
# BB quotes JPY per 100 units and the rest per 1; we store exactly what BB
# prints and let the UI label it, rather than silently rescaling.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_HEADING_DATE_RE = re.compile(r"Exchange Rate of Taka\s*\(([^)]+)\)", re.I)


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _published_date(soup) -> Optional[datetime.date]:
    """The date in the page heading — when BB published, not the trade day."""
    m = _HEADING_DATE_RE.search(soup.get_text(" ", strip=True))
    if not m:
        return None
    raw = m.group(1).strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    log.warning("fxrates: could not parse the heading date %r", raw)
    return None


def parse_fx_rates_html(html: str) -> List[Dict]:
    """Parse the USD and cross-rate tables from the daily page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    published = _published_date(soup)
    if published is None:
        log.error("fxrates: no publication date on the page — refusing to guess one")
        return []
    # The rates describe the previous business day (BB's own note).
    rate_date = calendar_utils.previous_working_day(published)

    rows: List[Dict] = []
    seen = set()
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            ccy = cells[0].strip().upper()
            if not _CURRENCY_RE.match(ccy) or ccy in seen:
                continue
            bid, ask = _parse_float(cells[1]), _parse_float(cells[2])
            if bid is None or ask is None:
                continue
            seen.add(ccy)
            rows.append({
                "rate_date":      rate_date,
                "published_date": published,
                "currency":       ccy,
                "bid_rate":       bid,
                "ask_rate":       ask,
                # Only USD carries a weighted-average rate.
                "war_rate":       _parse_float(cells[3]) if len(cells) >= 4 else None,
            })
    return rows


def fetch_fx_rates() -> List[Dict]:
    """Today's published page → one row per currency for the previous business day.

    BB serves only the current snapshot here, so history accrues one day at a
    time from this fetch running on schedule.
    """
    from curl_cffi import requests as creq
    try:
        r = creq.get(_URL, impersonate="chrome", timeout=40)
        r.raise_for_status()
        rows = parse_fx_rates_html(r.text)
        log.info("FX rates: %d currencies for %s",
                 len(rows), rows[0]["rate_date"] if rows else "—")
        return rows
    except Exception as exc:
        log.error("FX rates fetch failed: %s", exc)
        return []
