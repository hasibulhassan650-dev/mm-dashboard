"""
fetchers/gsom.py — Fetch live HTML from all three GSOM MTM pages.

Each page shows ALL currently active securities (not yet matured).
We always do a fresh HTTP fetch on every pipeline run so new ISINs
and updated outstanding amounts are captured immediately.

Pages (BB restructured the site ~July 2026; old /mtm*.php URLs are 404):
  T-Bond : https://gsom.bb.org.bd/index.php/tbond
  FRTB   : https://gsom.bb.org.bd/index.php/frtb
  T-Bill : https://gsom.bb.org.bd/index.php/tbill

The new pages default to TODAY's settlement date, which renders an
empty table on holidays/weekends or before data is posted. When the
GET comes back without ISIN rows we re-POST the form with
picker_date=DD-MON-YY, stepping back one day at a time until rows
appear.
"""
import logging
import datetime
import re
import time
from io import BytesIO
from typing import Optional

from config import (
    GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL,
    GSOM_FRTB_DL_URL,
    REQUEST_TIMEOUT, REQUEST_RETRIES, REQUEST_BACKOFF,
)
from fetchers.http_client import fetch_html, fetch_bytes, get_session

log = logging.getLogger(__name__)

_HTML_URLS = {
    "T_BOND": GSOM_TBOND_URL,
    "FRTB":   GSOM_FRTB_URL,
    "T_BILL": GSOM_TBILL_URL,
}

_ISIN_RE = re.compile(r"BD\d{10}")

# How many calendar days to walk back looking for a settlement date with data
_MAX_LOOKBACK_DAYS = 10


def _has_isin_rows(html: str) -> bool:
    return bool(html) and _ISIN_RE.search(html) is not None


def _fetch_with_date_fallback(url: str) -> str:
    """
    GET the page; if the default (today's) settlement date has no rows,
    POST picker_date for previous days until a populated table appears.
    Uses one session so the CodeIgniter CSRF cookie carries over.
    """
    session = get_session()
    last_exc = None
    html = ""
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            html = resp.text
            break
        except Exception as exc:
            last_exc = exc
            wait = REQUEST_BACKOFF * attempt
            log.warning("Attempt %d failed for %s: %s. Waiting %ds", attempt, url, exc, wait)
            time.sleep(wait)
    else:
        raise last_exc

    if _has_isin_rows(html):
        return html

    d = datetime.date.today()
    for _ in range(_MAX_LOOKBACK_DAYS):
        d -= datetime.timedelta(days=1)
        picker = d.strftime("%d-%b-%y").upper()      # e.g. 02-JUL-26
        try:
            resp = session.post(url, data={"picker_date": picker, "ci_csrf_token": ""},
                                timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            log.warning("POST %s picker_date=%s failed: %s", url, picker, exc)
            continue
        if _has_isin_rows(resp.text):
            log.info("GSOM %s: empty for today, got data for %s", url, picker)
            return resp.text

    log.warning("GSOM %s: no populated settlement date in last %d days",
                url, _MAX_LOOKBACK_DAYS)
    return html


def fetch_gsom_html(security_type: str, date: datetime.date = None) -> dict:
    """
    Fetch the live GSOM page for a given security type.
    Always returns the full current table — every ISIN on the page.

    Returns:
        {"html": str, "source_url": str, "method": "HTML"}

    If the fetch fails, raises an exception — the pipeline logs it and
    continues with other page types.
    """
    html_url = _HTML_URLS[security_type]

    # For FRTB, try the XLS API first (more reliable, full data)
    if security_type == "FRTB" and date is not None:
        date_str = date.strftime("%Y%m%d")
        try:
            raw = fetch_bytes(GSOM_FRTB_DL_URL, params={"date": date_str})
            if raw and len(raw) > 100:
                import pandas as pd
                df = pd.read_excel(BytesIO(raw), header=0)
                log.info("FRTB XLS download succeeded for %s (%d rows)", date_str, len(df))
                return {
                    "dataframe":  df,
                    "source_url": f"{GSOM_FRTB_DL_URL}?date={date_str}",
                    "method":     "XLS",
                }
        except Exception as exc:
            log.warning("FRTB XLS failed (%s), falling back to HTML", exc)

    # Standard path: fetch the HTML page directly, walking back to the
    # most recent settlement date that has data.
    log.info("Fetching %s from %s", security_type, html_url)
    html = _fetch_with_date_fallback(html_url)

    if not html or len(html) < 500:
        raise ValueError(
            f"GSOM returned empty or too-short response for {security_type} "
            f"({len(html) if html else 0} bytes). Page may be down."
        )

    log.info("Fetched %s: %d bytes", security_type, len(html))
    return {"html": html, "source_url": html_url, "method": "HTML"}
