"""
fetchers/gsom.py — Fetch raw HTML from the three GSOM MTM pages.

Tries the XLS download API first (faster, more stable).
Falls back to HTML scraping if the download endpoint returns non-200 or
unexpected content type.

Returns raw HTML string — parsing is done in parsers/securities.py.
"""
import logging
import datetime
from io import BytesIO
from typing import Optional
import pandas as pd

from config import (
    GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL,
    GSOM_FRTB_DL_URL, GSOM_TBOND_DL_URL, GSOM_TBILL_DL_URL,
)
from fetchers.http_client import fetch_html, fetch_bytes

log = logging.getLogger(__name__)

_DL_ENDPOINTS = {
    "T_BOND": (GSOM_TBOND_DL_URL, GSOM_TBOND_URL),
    "FRTB":   (GSOM_FRTB_DL_URL,  GSOM_FRTB_URL),
    "T_BILL": (GSOM_TBILL_DL_URL,  GSOM_TBILL_URL),
}


def fetch_gsom_html(security_type: str, date: datetime.date = None) -> dict:
    """
    Fetch GSOM data for a given security type.
    Returns {"html": str, "source_url": str, "method": "XLS"|"HTML"}.

    Args:
        security_type: "T_BOND" | "FRTB" | "T_BILL"
        date: settlement date to request; None = server default (today's latest)
    """
    dl_url, html_url = _DL_ENDPOINTS[security_type]
    date_str = date.strftime("%Y%m%d") if date else None

    # ── Try XLS download endpoint ─────────────────────────────────────────────
    if date_str:
        try:
            raw = fetch_bytes(dl_url, params={"date": date_str})
            # XLS returns binary — convert to DataFrame then to dict list
            df = pd.read_excel(BytesIO(raw), header=0)
            log.info("XLS download succeeded for %s %s", security_type, date_str)
            return {"dataframe": df, "source_url": f"{dl_url}?date={date_str}", "method": "XLS"}
        except Exception as exc:
            log.warning("XLS download failed for %s: %s. Falling back to HTML.", security_type, exc)

    # ── Fall back to HTML scrape ──────────────────────────────────────────────
    html = fetch_html(html_url)
    return {"html": html, "source_url": html_url, "method": "HTML"}
