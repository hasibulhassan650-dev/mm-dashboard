"""
fetchers/interbank_fx.py — BB interbank FX market turnover (spot / forward / swap).

TARGET: https://www.bb.org.bd/en/index.php/fxmarket/interbank_fxmarket

This is the USD/BDT interbank market: how much actually traded, and where.
The SPOT table carries the rates (highest / lowest / weighted-average), which is
the closest thing to a market mid-rate BB publishes. FORWARD and SWAP report
turnover only — BB prints no rates for them, so those columns stay NULL rather
than being back-filled with the spot rate.

PAGE LAYOUT — the default GET renders all three tables, each under its own
heading ("FX Spot", "FX Forward", "FX SWAP"):
    FX Spot     : Date | Number of Deals | Volume ($m) | Highest | Lowest | WAR
    FX Forward  : Date | Number of Deals | Volume ($m)
    FX SWAP     : Date | Number of Deals | Volume ($m)
A POST of {fxmarketname, select_month, select_year} returns ONE table for that
segment and month — that is how history back to 2023 is reached.

Unlike most BB pages this one passes on TLS impersonation alone (same as
fetchers/monetary.py), so no Chrome/F5 hop is needed and the fetch stays fast.
"""
import datetime
import logging
import re
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/fxmarket/interbank_fxmarket"
SEGMENTS = ("SPOT", "FORWARD", "SWAP")
# BB's own labels, lower-cased, in the heading above each table.
_HEADING_SEGMENT = (("swap", "SWAP"), ("forward", "FORWARD"), ("spot", "SPOT"))


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


def _parse_date(s: str) -> Optional[datetime.date]:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _segment_of(table, fallback: Optional[str]) -> Optional[str]:
    """Which segment a table belongs to, read from the heading above it.

    Heading text is authoritative because the single-table POST response has no
    fixed index, and spot/forward/swap cannot be told apart by column count
    alone (forward and swap are identical three-column tables).
    """
    for prev in table.find_all_previous(["h1", "h2", "h3", "h4", "h5", "b", "strong", "caption", "legend"], limit=6):
        text = prev.get_text(" ", strip=True).lower()
        if not text or len(text) > 60:
            continue
        for needle, seg in _HEADING_SEGMENT:      # swap first: "FX SWAP" also contains no 'spot'
            if needle in text:
                return seg
    return fallback


def parse_interbank_fx_html(html: str, segment_hint: Optional[str] = None) -> List[Dict]:
    """Parse every FX turnover table on a page. `segment_hint` labels a
    single-table POST response when its heading is missing."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    rows: List[Dict] = []
    for table in soup.find_all("table"):
        seg = _segment_of(table, segment_hint)
        if seg not in SEGMENTS:
            continue
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            trade_date = _parse_date(cells[0])
            if trade_date is None:            # header row, or a stray label
                continue
            row = {
                "trade_date":     trade_date,
                "segment":        seg,
                "num_deals":      _parse_int(cells[1]),
                "volume_usd_mn":  _parse_float(cells[2]),
                "high_rate":      None,
                "low_rate":       None,
                "war_rate":       None,
            }
            # Only SPOT prints rates. Never invent them for forward/swap.
            if seg == "SPOT" and len(cells) >= 6:
                hi, lo = _parse_float(cells[3]), _parse_float(cells[4])
                war = _parse_float(cells[5])
                # BB occasionally prints the two extremes in the wrong columns
                # (09-Aug-2026: "highest" 123.75, "lowest" 123.79). The WAR sits
                # between them on every row ever checked, which proves both
                # NUMBERS are right and only the LABELS are swapped — so relabel
                # by size and say so. Same principle as the OMO rate fingerprint:
                # correct the label, never invent or discard a number.
                if hi is not None and lo is not None and hi < lo:
                    log.warning("Interbank FX %s: BB printed highest %.4f below lowest %.4f "
                                "— relabelling by size (WAR %.4f lies between them)",
                                trade_date, hi, lo, war if war is not None else float("nan"))
                    hi, lo = lo, hi
                row["high_rate"] = hi
                row["low_rate"]  = lo
                row["war_rate"]  = war
            rows.append(row)
    return rows


def _get(session, url, data=None):
    r = session.post(url, data=data, timeout=40) if data else session.get(url, timeout=40)
    r.raise_for_status()
    return r.text


def fetch_interbank_fx(months_back: int = 2) -> List[Dict]:
    """Current page plus `months_back` months of history, all three segments.

    months_back=0 fetches only the default page (today's month, all segments) —
    enough for the hourly refresh; a backfill passes a larger number.
    """
    from curl_cffi import requests as creq

    rows: List[Dict] = []
    try:
        s = creq.Session(impersonate="chrome")
        rows.extend(parse_interbank_fx_html(_get(s, _URL)))
        log.info("Interbank FX: %d rows from the current page", len(rows))

        today = datetime.date.today()
        for back in range(1, max(0, months_back) + 1):
            month = today.month - back
            year = today.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            for seg in SEGMENTS:
                try:
                    html = _get(s, _URL, {
                        "fxmarketname": seg,
                        "select_month": f"{month:02d}",
                        "select_year":  str(year),
                        "fxmarket_submit": "Search",
                    })
                    got = parse_interbank_fx_html(html, segment_hint=seg)
                    rows.extend(got)
                    log.info("Interbank FX: %s %04d-%02d -> %d rows", seg, year, month, len(got))
                except Exception as exc:
                    log.warning("Interbank FX: %s %04d-%02d failed: %s", seg, year, month, exc)
    except Exception as exc:
        log.error("Interbank FX fetch failed: %s", exc)
        return rows

    # The default page and a month query can both return the same day.
    dedup = {(r["trade_date"], r["segment"]): r for r in rows}
    return list(dedup.values())
