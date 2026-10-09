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
import calendar_utils
import logging
import re
from typing import Dict, List, Optional

from config import BB_TREASURY_URL, SEEDS_DIR

from fetchers import source_watch as SW

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

_ISIN_RE = re.compile(r"\b(BD[0-9A-Z]{10})\b")
# BB writes the re-issue marker inside the ISIN cell itself, e.g.
#   "BD0929441204 (Re-issuance: 2.73 Yr.)"
# That is the ONLY place a re-issue is flagged, and it is what makes one ISIN
# legitimately appear at several issue dates with different cut-offs.
_REISSUE_RE = re.compile(r"re[-\s]?issu\w*\s*[:\-]?\s*([0-9.]+\s*\w*)", re.I)


def _parse_isin_cell(cell: str):
    """-> (isin, is_reissue, remaining_label). Any part may be None/False."""
    if not cell:
        return None, False, None
    m = _ISIN_RE.search(cell.replace(" ", ""))
    isin = m.group(1) if m else None
    r = _REISSUE_RE.search(cell)
    return isin, bool(r), (r.group(1).strip() if r else None)


def _clean_int(s):
    v = _clean_float(s)
    return int(v) if v is not None else None


def _range_text(s):
    """A yield range like '8.7280-11.7100' is kept verbatim: it is two numbers
    and collapsing it to one would invent a figure BB did not print."""
    s = (s or "").strip()
    return s or None


# ── What BB calls each column ────────────────────────────────────────────────
# The old code collected these header texts and then mapped columns by
# POSITION, branched on cell count ("if n >= 14"). Insert one column upstream
# and that branch still matches, so every field is read one place to the left:
# a sale value booked as a yield, a bid count booked as an amount. Nothing is
# missing and nothing is stale, so no freshness check can see it. That is the
# most dangerous failure this project has, and this list is the fix.
def _cell_at(cells: List[str], idx) -> Optional[str]:
    """One cell, by mapped index. None when the column is absent or short."""
    if idx is None or idx >= len(cells):
        return None
    return cells[idx]


# The structural fingerprints of the most recent fetch, newest page last. A
# module-level handoff rather than a changed return type: every caller of
# fetch_primary_yields_history() keeps working, and the pipeline — which is the
# only layer allowed to touch the database — registers them.
LAST_STRUCTURE: List[Dict] = []

TREASURY_COLUMNS = (
    SW.Column("issue",     ["issue date"], required=True, fallbacks=[["date"]]),
    SW.Column("isin",      ["isin"]),
    SW.Column("rem",       ["remaining maturity"], fallbacks=[["remaining"], ["rem"]]),
    SW.Column("name",      ["tenor and name"], fallbacks=[["tenor"], ["name"]]),
    SW.Column("nbids_rcv", ["received", "no of bids"]),
    SW.Column("offered",   ["received", "face value"], fallbacks=[["offered"]]),
    SW.Column("rng_rcv",   ["received", "range of yields"]),
    SW.Column("nbids_acc", ["accepted", "no of bids"]),
    SW.Column("accepted",  ["accepted", "face value"],
              fallbacks=[["accepted"], ["face value"]]),
    # Sale value is the cash actually paid and the one figure the ladder needs;
    # it sits BESIDE accepted face value with an almost identical header, which
    # is exactly the pair a positional slip swaps.
    SW.Column("sale",      ["accepted", "sale value"], fallbacks=[["sale value"]]),
    SW.Column("rng_acc",   ["accepted", "range of yields"]),
    SW.Column("wap",       ["weighted average price"], fallbacks=[["price"]]),
    SW.Column("cutoff",    ["cut off"], none_of=["price"], required=True),
    SW.Column("std",       ["devolvement yield"], fallbacks=[["standard"], ["std"]]),
)


def _parse_page(html: str, snapshot_date: datetime.date,
                meta: Dict = None) -> List[Dict]:
    """Parse one monthly results page.

    Raises source_watch.StructureChanged when BB's table no longer carries a
    column we require. That is deliberate: a refusal that names the missing
    header is recoverable in one line, and a plausible wrong number is not.

    `meta`, if given, is filled with the structural fingerprint of this fetch
    (selector, headers, rows seen / parsed / skipped and why) so the guardian
    can see BB move before the data goes wrong.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        if meta is not None:
            meta.update(SW.fingerprint("treasury", BB_TREASURY_URL, selector="table",
                                       error="no <table> element on the page"))
        return []

    results: List[Dict] = []
    skips: Dict[str, int] = {}

    def skip(reason: str):
        # A `continue` is invisible; a counted skip is a number the guardian can
        # compare. A parser quietly dropping most of a page used to look exactly
        # like a quiet week at BB.
        skips[reason] = skips.get(reason, 0) + 1

    # ── Table 0: full auction results ─────────────────────────────────────────
    all_rows = tables[0].find_all("tr")

    # The header is however many leading rows do NOT start with a date. BB uses
    # two levels ("Bids accepted" over seven sub-columns) and the first of them
    # has six cells, which is why "a row with >= 6 cells" alone cannot tell a
    # header from data.
    header_rows, data_rows = [], []
    for tr in all_rows:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        if not data_rows and _parse_date(cells[0].get_text(" ", strip=True)) is None:
            header_rows.append(tr)
        elif len(cells) >= 6:
            data_rows.append(tr)

    headers = SW.header_columns(header_rows)
    try:
        idx, notes = SW.map_columns(headers, TREASURY_COLUMNS, "treasury")
    except SW.StructureChanged as exc:
        if meta is not None:
            meta.update(SW.fingerprint("treasury", BB_TREASURY_URL, selector="table[0]",
                                       headers=headers, rows_seen=len(data_rows),
                                       rows_parsed=0, error=str(exc)))
        log.error("%s", exc)
        raise

    # Named after BB's own headers, so a reordered page maps itself. All
    # optional columns come out None when absent, never 0 — a zero here would
    # read as "no bids" or "sold at par".
    col_issue     = idx.get("issue")
    col_isin      = idx.get("isin")
    col_rem       = idx.get("rem")
    col_name      = idx.get("name")
    col_nbids_rcv = idx.get("nbids_rcv")
    col_offered   = idx.get("offered")
    col_rng_rcv   = idx.get("rng_rcv")
    col_nbids_acc = idx.get("nbids_acc")
    col_accepted  = idx.get("accepted")
    col_sale      = idx.get("sale")
    col_rng_acc   = idx.get("rng_acc")
    col_wap       = idx.get("wap")
    col_cutoff    = idx.get("cutoff")
    col_std       = idx.get("std")

    for tr in data_rows:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td","th"])]
        if len(cells) <= col_cutoff:
            skip("row has fewer cells than the cut-off column")
            continue

        issue_date = _parse_date(cells[col_issue])
        if not issue_date:
            skip("no issue date — BB prints blank devolvement rows")
            continue

        # Try Remaining Maturity column first; fall back to Tenor Name column
        rem_cell  = _cell_at(cells, col_rem)  or ""
        name_cell = _cell_at(cells, col_name) or ""
        tenor_label, stype = _rem_to_tenor(rem_cell)
        if not tenor_label:
            tenor_label, stype = _rem_to_tenor(name_cell)
        if not tenor_label:
            skip("no tenor in the remaining-maturity or tenor-and-name column")
            continue

        tenor_name = (_cell_at(cells, col_name) or "").lower()
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
        std_val    = _clean_float(_cell_at(cells, col_std))
        cutoff_val = _clean_float(_cell_at(cells, col_cutoff))
        cutoff = cutoff_val if (cutoff_val is not None and cutoff_val > 0) else std_val
        if cutoff is None or cutoff <= 0:
            skip("cut-off yield missing or not positive")
            continue

        isin, is_reissue, reissue_rem = _parse_isin_cell(_cell_at(cells, col_isin) or "")

        # tenor_label above comes from BB's REMAINING-maturity column, so a
        # re-issued 20-year bond with 2.73 years left is labelled 2Y. That is
        # right for the yield curve and wrong for an instrument listing,
        # where the security is a 20-year bond. Keep BB's "Tenor and name"
        # reading too; the two are different facts, not a correction.
        orig_label, _orig_type = _rem_to_tenor(name_cell)
        orig_years = _tenor_years(orig_label) if orig_label else None

        results.append({
            "snapshot_date":     snapshot_date,
            # The page prints the ISSUE date; the auction was the previous
            # working day (bills: Sun→Mon, bonds: Tue→Wed, holidays skipped).
            "auction_date":      (calendar_utils.previous_working_day(issue_date)
                                  if issue_date else snapshot_date),
            "issue_date":        issue_date,
            "security_type":     stype,
            "tenor_label":       tenor_label,
            "tenor_years":       _tenor_years(tenor_label),
            "cutoff_yield_pct":  cutoff,
            "offered_bdt_crore": _clean_float(_cell_at(cells, col_offered)),
            "accepted_bdt_crore":_clean_float(_cell_at(cells, col_accepted)),
            # ── fields BB prints that were previously discarded ───────────
            "isin":                 isin,
            "is_reissue":           is_reissue,
            "reissue_remaining":    reissue_rem,
            "original_tenor_label": orig_label,
            "original_tenor_years": orig_years,
            # SALE value is the cash the market actually paid; the accepted
            # FACE value above is the par amount. On a premium re-issue they
            # differ materially (02-Sep-2026 20Y: face 3,500.00, sale
            # 3,689.28), and only the sale value is a cash flow.
            "sale_value_bdt_crore": _clean_float(_cell_at(cells, col_sale)),
            "bids_received_count":  _clean_int(_cell_at(cells, col_nbids_rcv)),
            "bids_accepted_count":  _clean_int(_cell_at(cells, col_nbids_acc)),
            "yield_range_received": _range_text(_cell_at(cells, col_rng_rcv)),
            "yield_range_accepted": _range_text(_cell_at(cells, col_rng_acc)),
            "weighted_avg_price":   _clean_float(_cell_at(cells, col_wap)),
            "devolvement_yield_pct": std_val,
            "source": BB_TREASURY_URL,
        })

    # ── Table 1 (Standard Tenor & Yield) is deliberately NOT used ─────────────
    # It carries BB's interpolated STANDARD-tenor benchmark yields (e.g. an exact
    # 15Y point), which previously OVERWROTE each bond's real cut-off yield —
    # showing 10.36% where the 15Y auction actually cleared at 9.0975%. The desk
    # wants the auction cut-off, so the cut-off from Table 0 is kept as-is.

    if meta is not None:
        meta.update(SW.fingerprint(
            "treasury", BB_TREASURY_URL, selector="table[0]", headers=headers,
            rows_seen=len(data_rows), rows_parsed=len(results), skips=skips,
            unmapped=notes.get("unmapped"), mapped=idx))
    if skips:
        log.info("  skipped %d row(s): %s", sum(skips.values()), skips)
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
    # One structural fingerprint per page read, handed to the caller through
    # LAST_STRUCTURE so the pipeline can register what BB's page looked like
    # without this fetcher importing the database.
    structure: List[Dict] = []
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
        meta = {}
        try:
            rows = _parse_page(html, snap_date, meta)
        except SW.StructureChanged as exc:
            # One month BB has reshaped must not kill the whole fetch, and must
            # not be silent either: the fingerprint carries the reason, and the
            # guardian turns it into an escalation.
            structure.append(meta or SW.fingerprint(
                "treasury", BB_TREASURY_URL, error=str(exc)))
            log.error("  %s: %s", mstr, exc)
            continue
        if meta:
            structure.append(meta)
        log.info("  %s => %d rows", mstr, len(rows))
        all_rows.extend(rows)

    # Deduplicate: keep first occurrence per (tenor_label, auction_date)
    seen: Dict[tuple, Dict] = {}
    for r in all_rows:
        key = (r["tenor_label"], str(r["auction_date"]))
        if key not in seen:
            seen[key] = r
    result = sorted(seen.values(), key=lambda r: (r["tenor_label"], str(r["auction_date"])))
    LAST_STRUCTURE[:] = structure
    log.info("Treasury fetch complete: %d unique yield points", len(result))
    return result


def fetch_primary_yields(snapshot_date: datetime.date = None) -> List[Dict]:
    """Fetch only the latest/current month's yields (used by pipeline on each refresh)."""
    return fetch_primary_yields_history(months_back=1)
