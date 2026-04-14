"""
fetcher/gsom.py

Fetches raw HTML from GSOM pages (T-Bond, FRTB, T-Bill MTM).
Returns raw HTML strings only — no parsing here.

Design principles:
  - One function per source URL.
  - Retry with exponential backoff.
  - On failure, raise FetchError (never silently return empty).
  - Cache to disk keyed by (url, settlement_date) to avoid hammering BB.
"""

import hashlib
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from config import (
    GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL,
    HTTP_TIMEOUT, HTTP_HEADERS, ROOT,
)

logger = logging.getLogger(__name__)

CACHE_DIR = ROOT / "data" / "html_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MAX_RETRIES   = 3
RETRY_BACKOFF = 2.0   # seconds; doubles each retry


class FetchError(Exception):
    """Raised when a source page cannot be fetched after all retries."""
    pass


# ── Internal helpers ───────────────────────────────────────────────────────────

def _cache_path(url: str, settlement_date: Optional[date]) -> Path:
    key = f"{url}_{settlement_date or 'latest'}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    label = url.split("/")[-1].split(".")[0]
    fname = f"{label}_{settlement_date or 'latest'}_{h}.html"
    return CACHE_DIR / fname


def _fetch_with_retry(url: str, params: Optional[dict] = None) -> str:
    """
    GET url with retry. Returns response text.
    Raises FetchError after MAX_RETRIES failures.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=HTTP_HEADERS,
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            logger.info(
                "Fetched %s (attempt %d) — %d bytes",
                url, attempt, len(resp.content),
            )
            return resp.text
        except requests.RequestException as e:
            last_exc = e
            wait = RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "Fetch attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                attempt, MAX_RETRIES, url, e, wait,
            )
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    raise FetchError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_exc}")


def _load_cache(path: Path) -> Optional[str]:
    if path.exists():
        logger.debug("Cache hit: %s", path)
        return path.read_text(encoding="utf-8")
    return None


def _save_cache(path: Path, html: str) -> None:
    path.write_text(html, encoding="utf-8")
    logger.debug("Cached to: %s", path)


# ── Public fetch functions ─────────────────────────────────────────────────────

def fetch_tbond_html(settlement_date: Optional[date] = None, use_cache: bool = True) -> str:
    """
    Fetch T-Bond MTM page HTML.
    settlement_date: if None, fetches today's live snapshot.
    GSOM loads the most recent data by default on GET — no POST needed for latest.
    For historical dates, the page requires a date POST param (not supported here;
    use cache from a prior live fetch instead).
    """
    cache_path = _cache_path(GSOM_TBOND_URL, settlement_date)
    if use_cache:
        cached = _load_cache(cache_path)
        if cached:
            return cached

    html = _fetch_with_retry(GSOM_TBOND_URL)
    _save_cache(cache_path, html)
    return html


def fetch_frtb_html(settlement_date: Optional[date] = None, use_cache: bool = True) -> str:
    """Fetch FRTB MTM page HTML."""
    cache_path = _cache_path(GSOM_FRTB_URL, settlement_date)
    if use_cache:
        cached = _load_cache(cache_path)
        if cached:
            return cached

    html = _fetch_with_retry(GSOM_FRTB_URL)
    _save_cache(cache_path, html)
    return html


def fetch_tbill_html(settlement_date: Optional[date] = None, use_cache: bool = True) -> str:
    """Fetch T-Bill MTM page HTML."""
    cache_path = _cache_path(GSOM_TBILL_URL, settlement_date)
    if use_cache:
        cached = _load_cache(cache_path)
        if cached:
            return cached

    html = _fetch_with_retry(GSOM_TBILL_URL)
    _save_cache(cache_path, html)
    return html


def fetch_all(settlement_date: Optional[date] = None, use_cache: bool = True) -> dict:
    """
    Fetch all three GSOM pages. Returns dict keyed by instrument type.
    On partial failure, raises FetchError immediately.
    """
    return {
        "T_BOND": fetch_tbond_html(settlement_date, use_cache),
        "FRTB":   fetch_frtb_html(settlement_date, use_cache),
        "T_BILL": fetch_tbill_html(settlement_date, use_cache),
    }
