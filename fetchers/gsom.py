"""
fetchers/gsom.py — Fetch live HTML from all three GSOM MTM pages.

Each page shows ALL currently active securities (not yet matured).
We always do a fresh HTTP GET on every pipeline run so new ISINs
and updated outstanding amounts are captured immediately.

Pages:
  T-Bond : https://gsom.bb.org.bd/mtm.php
  FRTB   : https://gsom.bb.org.bd/mtm-frtb.php
  T-Bill : https://gsom.bb.org.bd/mtm-bill.php

The FRTB page has a known XLS download API.
T-Bond and T-Bill are HTML-only (XLS endpoints unconfirmed).
HTML scraping is the reliable path for all three.
"""
import logging
import datetime
from io import BytesIO
from typing import Optional

from config import (
    GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL,
    GSOM_FRTB_DL_URL,
)
from fetchers.http_client import fetch_html, fetch_bytes

log = logging.getLogger(__name__)

_HTML_URLS = {
    "T_BOND": GSOM_TBOND_URL,
    "FRTB":   GSOM_FRTB_URL,
    "T_BILL": GSOM_TBILL_URL,
}


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

    # Standard path: fetch the HTML page directly
    # The pages load the full table server-side — no JS rendering needed
    # for the default (latest) settlement date.
    log.info("Fetching %s from %s", security_type, html_url)
    html = fetch_html(html_url)

    if not html or len(html) < 500:
        raise ValueError(
            f"GSOM returned empty or too-short response for {security_type} "
            f"({len(html) if html else 0} bytes). Page may be down."
        )

    log.info("Fetched %s: %d bytes", security_type, len(html))
    return {"html": html, "source_url": html_url, "method": "HTML"}
