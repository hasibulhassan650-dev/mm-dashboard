"""
fetchers/interbank_repo.py — BB interbank repo market (secured overnight money).

TARGET: https://www.bb.org.bd/en/index.php/financialactivity/interbankrepo

The secured counterpart to call money, and the other half of the liquidity
picture. Note the tenor: BB's interbank repo trades 1-7 DAYS, not overnight, so
it sits slightly ABOVE overnight call money (observed +1 to +13bp) — that is
term premium, not a dislocation. The cross-source check in validate.py is
therefore a wide band around the call rate, not a "secured must be cheaper" rule.

TABLE LAYOUT (one table, amounts in crore Tk):
    Date | Number of deals | Amount | Tenor (days) | Rate range (%) | WAR (%)
e.g. 24/09/2026 | 30 | 2979.244763419 | 1-7 | 8. 50-8.95 | 8.8

BB's own output is untidy and the parser must survive it verbatim:
  * "8. 50-8.95"      — a stray space inside a number
  * "2979.244763419"  — float noise from their spreadsheet
  * "1-7"             — a tenor RANGE, not a single tenor
So ranges are split into explicit min/max columns; nothing is rounded away on
the way in, and a row whose numbers cannot be read is dropped loudly rather
than stored half-parsed.

The default page (≈1 month) passes on TLS impersonation alone — no Chrome hop.
"""
import datetime
import logging
import re
from typing import List, Dict, Optional, Tuple

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/financialactivity/interbankrepo"
# A range like "8. 50-8.95", "8.00-9.25" or a bare "8.8". Spaces are stripped
# before matching, so BB's stray space inside a number cannot split a field.
_NUM = r"-?\d+(?:\.\d+)?"


def _clean(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(_clean(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(float(_clean(s).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _parse_range(s: str) -> Tuple[Optional[float], Optional[float]]:
    """'1-7' → (1, 7); '8. 50-8.95' → (8.5, 8.95); '8.8' → (8.8, 8.8)."""
    txt = _clean(s).replace(",", "")
    if not txt:
        return None, None
    m = re.fullmatch(rf"({_NUM})-({_NUM})", txt)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    single = _parse_float(txt)
    return (single, single)


def _parse_date(s: str) -> Optional[datetime.date]:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_interbank_repo_html(html: str) -> List[Dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    rows: List[Dict] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 6:
                continue
            trade_date = _parse_date(cells[0])
            if trade_date is None:                  # header / caption row
                continue
            tenor_min, tenor_max = _parse_range(cells[3])
            rate_min, rate_max = _parse_range(cells[4])
            war = _parse_float(cells[5])
            amount = _parse_float(cells[2])
            if amount is None:
                log.warning("interbank repo: unreadable row for %s: %r", trade_date, cells)
                continue
            deals = _parse_int(cells[1])
            # A no-trade day: BB prints a row of literal zeros (15-Sep-2026:
            # "0 0 0 0 0"). Zero deals is a FACT worth keeping, but there was no
            # rate and no tenor that day — storing 0.0 would assert repo traded
            # at 0% and drag every average and chart down with it. A real figure
            # or no figure.
            no_trade = not deals and not amount
            rows.append({
                "trade_date":     trade_date,
                "num_deals":      deals,
                "amount_crore":   amount,
                "tenor_min_days": None if no_trade else (int(tenor_min) if tenor_min is not None else None),
                "tenor_max_days": None if no_trade else (int(tenor_max) if tenor_max is not None else None),
                "rate_min_pct":   None if no_trade else rate_min,
                "rate_max_pct":   None if no_trade else rate_max,
                "war_pct":        None if no_trade else war,
            })
    return rows


def fetch_interbank_repo() -> List[Dict]:
    """The default page — roughly the last month of trading days."""
    from curl_cffi import requests as creq
    try:
        r = creq.get(_URL, impersonate="chrome", timeout=40)
        r.raise_for_status()
        rows = parse_interbank_repo_html(r.text)
        log.info("Interbank repo: parsed %d rows", len(rows))
        return rows
    except Exception as exc:
        log.error("Interbank repo fetch failed: %s", exc)
        return []
