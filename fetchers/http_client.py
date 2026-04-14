"""fetchers/http_client.py — Shared HTTP session with retry logic."""
import logging, time, requests
from config import REQUEST_HEADERS, REQUEST_TIMEOUT, REQUEST_RETRIES, REQUEST_BACKOFF

log = logging.getLogger(__name__)

def get_session():
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    return s

def fetch_html(url: str, params: dict = None) -> str:
    """Fetch URL, return HTML text. Retries on failure."""
    session = get_session()
    last_exc = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            log.info("Fetched %s (%d bytes)", url, len(resp.content))
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            wait = REQUEST_BACKOFF * attempt
            log.warning("Attempt %d failed for %s: %s. Waiting %ds", attempt, url, exc, wait)
            time.sleep(wait)
    raise last_exc

def fetch_bytes(url: str, params: dict = None) -> bytes:
    """Fetch binary content (XLS downloads)."""
    session = get_session()
    resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content
