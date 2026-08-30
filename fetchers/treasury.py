"""
fetchers/treasury.py — Live fetch primary market yields from BB Treasury Results page.

TARGET: https://www.bb.org.bd/en/index.php/monetaryactivity/treasury

The page uses F5/Shape Security bot detection AND a POST form with date_picker.
Strategy:
  1. Open Chrome once via undetected-chromedriver (bypasses bot challenge)
  2. For each requested month, fill date_picker and submit the form
  3. Parse both tables per month and return all rows

Returned rows (list of dicts):
  snapshot_date, auction_date, security_type, tenor_label,
  tenor_years, cutoff_yield_pct, offered_bdt_crore, accepted_bdt_crore, source
"""
import datetime
import logging
import re
from typing import Dict, List, Optional

from config import BB_TREASURY_URL, SEEDS_DIR

log = logging.getLogger(__name__)

_TENOR_YEARS: Dict[str, float] = {
    "14D": 14/365.25, "91D": 91/365.25, "182D": 182/365.25, "364D": 364/365.25,
    "2Y": 2.0, "3Y_FRTB": 3.0, "5Y": 5.0, "10Y": 10.0,
    "15Y": 15.0, "20Y": 20.0, "25Y": 25.0,
}

def _tenor_years(label: str) -> Optional[float]:
    if label in _TENOR_YEARS:
        return _TENOR_YEARS[label]
    m = re.match(r"^(\d+)D$", label)
    if m: return int(m.group(1)) / 365.25
    m = re.match(r"^(\d+)Y", label)
    if m: return float(m.group(1))
    return None

def _clean_float(s: str) -> Optional[float]:
    try: return float(str(s).replace(",", "").strip())
    except: return None

def _parse_date(s: str) -> Optional[datetime.date]:
    for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d", "%d-%b-%y"):
        try: return datetime.datetime.strptime(s.strip(), fmt).date()
        except: continue
    return None

def _rem_to_tenor(s: str):
    """Parse tenor from remaining-maturity OR tenor-name column.
    Handles: '91 days', '10yr', '20yr T.Bond', '3yr FRT.Bond', '91 days T.Bill'"""
    raw = s.strip().lower()
    # Days → T-Bill
    m = re.match(r"^(\d+)\s*days?", raw)
    if m: return f"{m.group(1)}D", "T_BILL"
    # FRTB first (before generic year match)
    if "frt" in raw or "frtb" in raw:
        m = re.match(r"^(\d+)\s*yr", raw)
        y = int(m.group(1)) if m else 3
        return f"{y}Y_FRTB", "FRTB"
    # Years → T-Bond  (e.g. "10yr", "20yr T.Bond", "2yr")
    m = re.match(r"^(\d+)\s*(?:yr|year)s?", raw)
    if m:
        y = int(m.group(1))
        return f"{y}Y", "T_BOND"
    return None, None

def _parse_page(html: str, snapshot_date: datetime.date) -> List[Dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    results: List[Dict] = []

    # ── Table 0: full auction results ─────────────────────────────────────────
    # Column layout varies between current-month and historical views.
    # Detect "Cut off yield" and "Issue date" columns dynamically from headers.
    all_rows = tables[0].find_all("tr")

    # Flatten the first two header rows into one list to find column names
    header_cells: List[str] = []
    for tr in all_rows[:3]:
        for td in tr.find_all(["th", "td"]):
            t = td.get_text(" ", strip=True).lower()
            if t:
                header_cells.append(t)

    # Locate column indices by keyword scan of header text
    col_issue   = None   # "Issue date"  / first date-like column
    col_rem     = None   # "Remaining maturity"
    col_name    = None   # "Tenor and name"
    col_cutoff  = None   # "Cut off yield"
    col_std     = None   # "Standard / devolvement yield"
    col_offered = None
    col_accepted= None

    # Scan data rows to infer columns (more reliable than header for merged cells)
    # Strategy: find the first data row and deduce columns positionally
    data_rows = [tr for tr in all_rows if len(tr.find_all(["td","th"])) >= 6]

    for tr in data_rows:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td","th"])]
        n = len(cells)
        # Look for a row where cells[0] looks like a date
        if _parse_date(cells[0]) is not None:
            # Found a data row — determine layout based on column count
            if n >= 14:
                # Full layout: Issue|ISIN|RemMat|TenorName|BidsRcvd(3)|BidsAcpt(5)|WtdAvgPx|Cutoff|Std
                col_issue   = 0
                col_rem     = 2
                col_name    = 3
                col_offered = 5
                col_accepted= 8
                col_cutoff  = 12
                col_std     = 13
            elif n >= 9:
                # Compact layout (some historical pages): Issue|ISIN|RemMat|TenorName|Offered|Accepted|Price|Cutoff|Std?
                col_issue   = 0
                col_rem     = 2
                col_name    = 3
                col_offered = 4
                col_accepted= 5
                col_cutoff  = 7
                col_std     = 8 if n > 8 else None
            elif n >= 6:
                # Minimal layout: Issue|ISIN|RemMat|TenorName|...|Cutoff
                col_issue   = 0
                col_rem     = 2
                col_name    = 3
                col_cutoff  = n - 1  # last column
            break

    if col_cutoff is None:
        log.warning("Could not detect column layout (%d cols) — skipping table", n if data_rows else 0)
    else:
        for tr in data_rows:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td","th"])]
            if len(cells) <= col_cutoff:
                continue

            issue_date = _parse_date(cells[col_issue])
            if not issue_date:
                continue   # skip "Devolvement" blank rows

            # Try Remaining Maturity column first; fall back to Tenor Name column
            rem_cell  = cells[col_rem]  if col_rem  < len(cells) else ""
            name_cell = cells[col_name] if col_name < len(cells) else ""
            tenor_label, stype = _rem_to_tenor(rem_cell)
            if not tenor_label:
                tenor_label, stype = _rem_to_tenor(name_cell)
            if not tenor_label:
                continue

            tenor_name = cells[col_name].lower() if col_name < len(cells) else ""
            if "t.bill" in tenor_name or "tbill" in tenor_name:
                stype = "T_BILL"
            elif "frt" in tenor_name:
                stype = "FRTB"
                # rem_cell gives "3Y" but label must be "3Y_FRTB" — fix the label too
                if not tenor_label.endswith("_FRTB"):
                    m = re.match(r"^(\d+)", tenor_label)
                    tenor_label = f"{m.group(1)}Y_FRTB" if m else "3Y_FRTB"
            elif "t.bond" in tenor_name or "tbond" in tenor_name:
                stype = "T_BOND"

            # Store the CUT-OFF yield — the yield at which the auction actually
            # cleared, which is what the desk trades off. The Standard/Devolvement
            # Yield column (col_std) is a DIFFERENT figure (the rate on any amount
            # devolved on the PDs/BB) and for a premium re-issued bond sits well
            # above the cut-off — e.g. 15Y on 27-Aug-2026: cut-off 9.0975% vs
            # standard 10.36%. Prefer the cut-off; fall back to std only if the
            # cut-off column is missing. A 0% sovereign yield is never real.
            std_val    = _clean_float(cells[col_std])    if col_std and col_std < len(cells) else None
            cutoff_val = _clean_float(cells[col_cutoff]) if col_cutoff < len(cells) else None
            cutoff = cutoff_val if (cutoff_val is not None and cutoff_val > 0) else std_val
            if cutoff is None or cutoff <= 0:
                continue

            results.append({
                "snapshot_date":     snapshot_date,
                "auction_date":      issue_date or snapshot_date,
                "security_type":     stype,
                "tenor_label":       tenor_label,
                "tenor_years":       _tenor_years(tenor_label),
                "cutoff_yield_pct":  cutoff,
                "offered_bdt_crore": _clean_float(cells[col_offered]) if col_offered and col_offered < len(cells) else None,
                "accepted_bdt_crore":_clean_float(cells[col_accepted]) if col_accepted and col_accepted < len(cells) else None,
                "source": BB_TREASURY_URL,
            })

    # ── Table 1 (Standard Tenor & Yield) is deliberately NOT used ─────────────
    # It carries BB's interpolated STANDARD-tenor benchmark yields (e.g. an exact
    # 15Y point), which previously OVERWROTE each bond's real cut-off yield —
    # showing 10.36% where the 15Y auction actually cleared at 9.0975%. The desk
    # wants the auction cut-off, so the cut-off from Table 0 is kept as-is.

    log.info("Parsed %d yield points from page (snapshot %s)", len(results), snapshot_date)
    return results


# ── Chrome version detection ──────────────────────────────────────────────────

def _chrome_major_version() -> Optional[int]:
    # Single source of truth (Windows registry + Linux/macOS `--version`).
    from fetchers.bb_session import get_chrome_version
    return get_chrome_version()


# ── Main historical fetch ─────────────────────────────────────────────────────

def fetch_primary_yields_history(months_back: int = 6) -> List[Dict]:
    """
    Fetch primary market yields for the last months_back months (including current).
    Opens Chrome once, passes the F5 bot challenge, then submits the date_picker form
    for EVERY month — including the current month.

    Root cause of prior bug: only the current month was read from the default page view,
    which shows only the most recent auction result. All months must be explicitly submitted
    to get the full list of auction results for that month.

    TSPD bot protection allows 2 form submits per fresh page load.
    Batching: 2 months per fresh load. Total page loads = ceil(months_back / 2).
    """
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
    except ImportError as e:
        log.warning("undetected-chromedriver not available: %s", e)
        return []

    import time

    # Build month list: (form_string, snap_date), current month first.
    # snap_date is used as the snapshot_date label on stored rows; we use
    # the 28th of the month as a stable within-month proxy.
    today = datetime.date.today()
    months: List[tuple] = []
    d = today.replace(day=1)
    for i in range(months_back):
        mstr = d.strftime("%B, %Y")
        snap = d.replace(day=min(28, today.day) if i == 0 else 28)
        months.append((mstr, snap))
        d = (d - datetime.timedelta(days=1)).replace(day=1)  # go back one month

    return fetch_primary_yields_months(months)


def month_range(start: datetime.date, end: datetime.date) -> List[tuple]:
    """Build a [(form_string, snapshot_date)] list for every month start..end inclusive."""
    months: List[tuple] = []
    d = start.replace(day=1)
    last = end.replace(day=1)
    while d <= last:
        months.append((d.strftime("%B, %Y"), d.replace(day=28)))
        d = (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)  # next month
    return months


def fetch_primary_yields_months(months: List[tuple]) -> List[Dict]:
    """
    Fetch primary-market yields for an explicit list of (form_string, snapshot_date)
    months via the curl_cffi F5 bypass.

    Strategy (same proven pattern as the refrate/remittance fetchers): clear the F5
    JS challenge ONCE with Chrome to capture session cookies, then POST the
    date_picker form for many months using curl_cffi with Chrome's TLS fingerprint.
    Cookies are re-captured every RECAPTURE_EVERY posts (or whenever a response is
    blocked). This avoids relaunching Chrome per month — which is slow and flaky in
    CI and was failing the F5 challenge ~15% of the time on long runs.

    RECAPTURE_EVERY is tunable via env TREASURY_POSTS_PER_CAPTURE.
    """
    import os
    import time
    from fetchers.bb_session import get_f5_cookies, bb_post

    # 12 verified safe (a full year of months per F5 cookie capture, zero blocks)
    RECAPTURE_EVERY = int(os.environ.get("TREASURY_POSTS_PER_CAPTURE", "12"))
    log.info("Treasury fetch (curl_cffi): %d month(s), recapture every %d", len(months), RECAPTURE_EVERY)

    # Each failed capture is a full Chrome launch (~1-2 min). Without a cap, a
    # dead Chrome turns the remaining months into an hours-long crash-loop that
    # blows the CI job timeout — abort instead and return what we have.
    MAX_CONSECUTIVE_CAPTURE_FAILS = 3

    all_rows: List[Dict] = []
    state = {"cookies": None, "ua": None, "posts": 0, "fails": 0}

    def capture() -> bool:
        try:
            state["cookies"], state["ua"] = get_f5_cookies(
                BB_TREASURY_URL, wait_selector="input[name='date_picker']")
            state["posts"] = 0
            state["fails"] = 0
            return True
        except Exception as exc:
            log.error("  F5 cookie capture failed: %s", exc)
            state["cookies"] = None
            state["fails"] += 1
            return False

    def post(mstr: str):
        return bb_post(BB_TREASURY_URL, {"date_picker": mstr}, state["cookies"], state["ua"])

    for mstr, snap_date in months:
        if state["fails"] >= MAX_CONSECUTIVE_CAPTURE_FAILS:
            log.error("Aborting treasury fetch: %d consecutive F5 capture failures "
                      "(Chrome unusable) — returning %d rows fetched so far",
                      state["fails"], len(all_rows))
            break
        if state["cookies"] is None or state["posts"] >= RECAPTURE_EVERY:
            if not capture():
                time.sleep(3)
                if not capture():
                    continue
        try:
            html = post(mstr)
            state["posts"] += 1
        except Exception as exc:
            log.warning("  %s: POST blocked (%s) — recapturing", mstr, exc)
            state["cookies"] = None
            if not capture():
                continue
            try:
                html = post(mstr); state["posts"] += 1
            except Exception as exc2:
                log.warning("  %s: still blocked (%s) — skipping", mstr, exc2)
                continue
        rows = _parse_page(html, snap_date)
        log.info("  %s => %d rows", mstr, len(rows))
        all_rows.extend(rows)

    # Deduplicate: keep first occurrence per (tenor_label, auction_date)
    seen: Dict[tuple, Dict] = {}
    for r in all_rows:
        key = (r["tenor_label"], str(r["auction_date"]))
        if key not in seen:
            seen[key] = r
    result = sorted(seen.values(), key=lambda r: (r["tenor_label"], str(r["auction_date"])))
    log.info("Treasury fetch complete: %d unique yield points", len(result))
    return result


def fetch_primary_yields(snapshot_date: datetime.date = None) -> List[Dict]:
    """Fetch only the latest/current month's yields (used by pipeline on each refresh)."""
    return fetch_primary_yields_history(months_back=1)
