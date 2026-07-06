"""
fetchers/auction_live.py — Live fetch from BB auction calendar.

TARGET URL: https://www.bb.org.bd/en/index.php/monetaryactivity/auc_calendar/1

PAGE STRUCTURE (two tables, both on same page):
  Table 1: "Treasury Bills Auction Calendar of FY20XX-XX"
    Fixed columns: Auction no | Auction Date | 14 days | 91 days | 182 days | 364 days | Total
    NOTE: new bill tenors (e.g. 182D removed, new one added) are detected automatically

  Table 2: "Treasury Bonds (BGTBs) Auction Calendar of FY20XX-XX"
    Fixed columns: Auction no | Auction Date | 2 yr | 5 yr | 10 yr | 15 yr | 20 yr | 3 yr(FRTB) | Total
    NOTE: new bond tenors (e.g. 25yr added) are detected automatically

PARSING RULES (exact, matching source data):
  - Skip any row where all tenor amounts are 0.00
  - Only create one row per non-zero tenor per auction date
  - Zero amounts are noise — ignore them completely
  - Total Amount column is used only for cross-validation
  - Auction numbers are taken exactly as shown (some reset mid-year)
  - Dates are in DD-Mon-YYYY format

NEW COLUMNS:
  - Tenor columns are read from the header row dynamically
  - If BB adds a new tenor (e.g. 25yr bond), it appears automatically
  - No code change needed — just re-run the pipeline

FALLBACK:
  If Playwright is unavailable or page fails → use CSV seed
"""
import logging
import datetime
import re
from typing import List, Dict, Optional, Tuple

from config import SEEDS_DIR, CRORE_TO_MILLION
from fetchers.auction_parser import parse_auction_csv

log = logging.getLogger(__name__)

_AUC_URL = "https://www.bb.org.bd/en/index.php/monetaryactivity/auc_calendar/1"


# ── Tenor column → (label, security_type) mapping ────────────────────────────
# This mapping is applied to whatever column headers the page shows.
# New columns added by BB are auto-detected if they match known patterns.
def _identify_tenor(header_text: str) -> Optional[Tuple[str, str]]:
    """
    Map a column header to (tenor_label, security_type).
    Returns None for non-tenor columns (Auction no, Date, Total).
    Handles new columns automatically via pattern matching.
    """
    h = header_text.strip().lower()
    h = re.sub(r"\s+", " ", h)

    # Skip non-tenor columns
    if any(x in h for x in ["auction no", "auction date", "date", "total", "sl"]):
        return None

    # T-Bill tenors: "14 days", "91 days", "182 days", "364 days"
    m = re.match(r"^(\d+)\s*days?$", h)
    if m:
        days = int(m.group(1))
        return (f"{days}D", "T_BILL")

    # T-Bond tenors: "2 yr", "5 yr", "10 yr", "15 yr", "20 yr", "25 yr" etc
    # Also handles "2yr", "2 year", "2-yr"
    m = re.match(r"^(\d+)\s*(?:yr|year)s?$", h)
    if m:
        years = int(m.group(1))
        label = f"{years}Y"
        return (label, "T_BOND")

    # FRTB: "3 yr(frtb)", "3yr frtb", "frtb", "3 yr (frtb)"
    if "frtb" in h:
        m = re.match(r"^(\d+)\s*yr", h)
        yr = int(m.group(1)) if m else 3
        return (f"{yr}Y_FRTB", "FRTB")

    log.debug("Unrecognised tenor column header: '%s'", header_text)
    return None


def _parse_date_str(s: str) -> Optional[str]:
    """Parse DD-Mon-YYYY → YYYY-MM-DD. Returns None on failure."""
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%b-%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_amount(s: str) -> float:
    """Parse '3,500.00' → 3500.0. Returns 0.0 on failure."""
    try:
        return float(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _extract_fiscal_year(text: str) -> str:
    """Extract '2025-26' from heading text."""
    m = re.search(r"FY\s*(\d{4})[-–](\d{2,4})", text, re.I)
    if m:
        y1, y2 = m.group(1), m.group(2)
        if len(y2) == 4:
            y2 = y2[-2:]
        return f"{y1}-{y2}"
    # Fallback: use current FY
    today = datetime.date.today()
    y = today.year
    if today.month >= 7:
        return f"{y}-{str(y+1)[-2:]}"
    return f"{y-1}-{str(y)[-2:]}"


def _normalize_div_layout(html: str) -> str:
    """
    BB's July-2026 site redesign renders the calendars as div grids
    (.table_caption / .row-header / .row-data with .column cells) instead of
    <table>. Rebuild equivalent <table> markup so the proven table parser
    keeps working unchanged. Input is returned as-is when real tables exist.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return html
    soup = BeautifulSoup(html, "lxml")
    if soup.find("table") or not soup.select("div.table_caption"):
        return html

    parts = []
    for cap in soup.select("div.table_caption"):
        cap_text = cap.get_text(" ", strip=True)
        header = cap.find_next(class_="row-header")
        if header is None:
            continue
        ths = "".join(f"<th>{c.get_text(' ', strip=True)}</th>"
                      for c in header.select(".column"))
        trs = []
        node = header
        while True:
            node = node.find_next_sibling()
            if node is None:
                break
            classes = node.get("class") or []
            if "row-data" in classes:
                tds = "".join(f"<td>{c.get_text(' ', strip=True)}</td>"
                              for c in node.select(".column"))
                trs.append(f"<tr>{tds}</tr>")
            elif "row-header" in classes or "table_caption" in classes:
                break
        parts.append(f"<p>{cap_text}</p><table><tr>{ths}</tr>{''.join(trs)}</table>")

    if not parts:
        return html
    log.info("Normalized div-grid calendar layout: %d section(s)", len(parts))
    return "<html><body>" + "".join(parts) + "</body></html>"


def _parse_tables_from_html(html: str) -> List[Dict]:
    """
    Parse the BB auction calendar HTML.
    Finds both the T-Bill and T-Bond tables.
    Reads column headers dynamically — handles new tenors automatically.
    Only creates rows for non-zero tenor amounts.
    Cross-validates row sum against Total Amount column.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("beautifulsoup4 not installed")
        return []

    html  = _normalize_div_layout(html)
    soup  = BeautifulSoup(html, "lxml")
    rows  = []
    today = datetime.date.today()
    fy    = f"{today.year}-{str(today.year+1)[-2:]}" if today.month >= 7 else f"{today.year-1}-{str(today.year)[-2:]}"

    tables = soup.find_all("table")
    log.info("Found %d tables on auction calendar page", len(tables))

    for table_idx, table in enumerate(tables):
        # Identify section heading above this table
        section_text = ""
        for elem in table.find_all_previous(["h4","h5","h6","strong","p","div"]):
            t = elem.get_text(" ", strip=True)
            if "treasury" in t.lower() and ("bill" in t.lower() or "bond" in t.lower()):
                section_text = t
                break

        if not section_text:
            # Try caption inside table
            cap = table.find("caption")
            if cap:
                section_text = cap.get_text(" ", strip=True)

        if not section_text:
            log.debug("Table %d: no section heading found — skipping", table_idx)
            continue

        is_bill = "bill" in section_text.lower()
        is_bond = "bond" in section_text.lower() or "bgtb" in section_text.lower()
        if not is_bill and not is_bond:
            continue

        section_label = "Treasury Bills" if is_bill else "Treasury Bonds"
        table_fy      = _extract_fiscal_year(section_text) or fy

        # ── Parse header row ──────────────────────────────────────────────────
        header_row = table.find("tr")
        if not header_row:
            continue

        headers = [th.get_text(" ", strip=True) for th in header_row.find_all(["th", "td"])]

        # Map column index → (tenor_label, security_type) or None
        # Also track which column is auction_no, date, total
        col_no    = None
        col_date  = None
        col_total = None
        tenor_cols = {}  # col_index → (tenor_label, security_type)

        for i, h in enumerate(headers):
            h_lower = h.strip().lower()
            if re.search(r"auction\s*no|^no$|^sl", h_lower):
                col_no = i
            elif re.search(r"auction\s*date|^date$", h_lower):
                col_date = i
            elif re.search(r"^total", h_lower):
                col_total = i
            else:
                result = _identify_tenor(h)
                if result:
                    tenor_cols[i] = result

        if col_date is None:
            log.warning("Table %d (%s): no date column found in %s",
                        table_idx, section_label, headers)
            continue

        log.info("Table %d — %s | FY %s | Tenor columns: %s",
                 table_idx, section_label, table_fy,
                 {headers[i]: v for i, v in tenor_cols.items()})

        # ── Parse data rows ───────────────────────────────────────────────────
        data_rows = table.find_all("tr")[1:]
        table_rows_added = 0

        for tr in data_rows:
            cells = [td.get_text(" ", strip=True)
                     for td in tr.find_all(["td", "th"])]

            if len(cells) <= col_date:
                continue

            # Auction date
            date_str = _parse_date_str(cells[col_date])
            if not date_str:
                continue   # skip header repeats or total rows

            # Auction number
            ano = ""
            if col_no is not None and col_no < len(cells):
                ano = cells[col_no].strip()

            # Stated total (for cross-validation)
            stated_total = 0.0
            if col_total is not None and col_total < len(cells):
                stated_total = _parse_amount(cells[col_total])

            # Per-tenor amounts — only non-zero
            computed_total = 0.0
            for col_i, (tenor_label, security_type) in tenor_cols.items():
                if col_i >= len(cells):
                    continue
                amt = _parse_amount(cells[col_i])
                if amt <= 0:
                    continue   # skip zeros — they are noise

                computed_total += amt
                rows.append({
                    "fiscal_year":            table_fy,
                    "auction_no":             (f"B{ano}" if is_bond else ano),
                    "auction_date":           date_str,
                    "instrument_type":        "BILL" if is_bill else "BOND",
                    "tenor_label":            tenor_label,
                    "security_type":          security_type,
                    "offered_bdt_crore":      amt,
                    "offered_amount_bdt_crore": amt,
                    "offered_amount_bdt_mill":  round(amt * CRORE_TO_MILLION, 4),
                    "accepted_bdt_crore":     "",
                    "accepted_amount_bdt_crore": None,
                    "accepted_amount_bdt_mill":  None,
                    "outflow_status":         "PLANNED",
                    "weighted_avg_yield_pct": None,
                    "resulting_isin":         None,
                    "source_section":         section_label,
                    "source":                 _AUC_URL,
                    "source_row_num":         0,
                    "row_total_bdt_crore":    stated_total,
                    "row_total_bdt_mill":     round(stated_total * CRORE_TO_MILLION, 4),
                })
                table_rows_added += 1

            # Cross-validate total
            if stated_total > 0 and abs(computed_total - stated_total) > 1.0:
                log.warning(
                    "%s auction %s %s: sum=%.2f stated=%.2f — mismatch",
                    section_label, ano, date_str, computed_total, stated_total
                )

        log.info("Table %d (%s): %d non-zero tenor rows from %d data rows",
                 table_idx, section_label, table_rows_added, len(data_rows))

    bills = [r for r in rows if r["security_type"] == "T_BILL"]
    bonds = [r for r in rows if r["security_type"] == "T_BOND"]
    frtbs = [r for r in rows if r["security_type"] == "FRTB"]
    log.info(
        "HTML parse complete: %d total rows | T-Bill: %d | T-Bond: %d | FRTB: %d",
        len(rows), len(bills), len(bonds), len(frtbs)
    )
    return rows


def _has_calendar_content(html: Optional[str]) -> bool:
    """
    Positive content check. NEVER test for TSPD/bobcmn absence — those
    strings linger in inline scripts on the REAL page too, which made every
    fetch path misreport "blocked" during the July 2026 incident.
    """
    if not html:
        return False
    low = html.lower()
    return ("auction calendar" in low) and ("row-data" in low or "<table" in low)


def _fetch_with_curl() -> Optional[str]:
    """
    Primary path: plain curl_cffi with Chrome TLS impersonation. Verified
    2026-07-06 to return the full calendar page — no Chrome required.
    """
    try:
        from curl_cffi import requests as cr
        r = cr.get(_AUC_URL, impersonate="chrome", timeout=30)
        if r.status_code == 200 and _has_calendar_content(r.text):
            log.info("curl_cffi: fetched auction calendar (%d bytes)", len(r.text))
            return r.text
        log.warning("curl_cffi: no calendar content (status=%s, %d bytes)",
                    r.status_code, len(r.text or ""))
    except Exception as exc:
        log.warning("curl_cffi auction calendar fetch failed: %s", exc)
    return None


def _fetch_with_chrome_f5() -> Optional[str]:
    """
    Fallback: load the page in Chrome directly and return page_source once
    calendar content appears. TSPD cookies are path-bound, so cookies
    captured on another BB page do not clear this URL — Chrome must
    navigate the calendar URL itself.
    """
    try:
        import time
        import undetected_chromedriver as uc
        from fetchers.bb_session import get_chrome_version

        options = uc.ChromeOptions()
        options.add_argument("--window-position=-10000,-10000")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = None
        try:
            driver = uc.Chrome(options=options, version_main=get_chrome_version())
            driver.get(_AUC_URL)
            html = ""
            for _ in range(18):          # up to ~90s for the challenge to clear
                time.sleep(5)
                html = driver.page_source or ""
                if _has_calendar_content(html):
                    log.info("Chrome: auction calendar loaded (%d bytes)", len(html))
                    return html
            log.warning("Chrome: calendar content never appeared (%d bytes)", len(html))
            return None
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
    except Exception as exc:
        log.error("Chrome auction calendar fetch failed: %s", exc)
        return None


def _fetch_with_playwright() -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None

    html = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page    = browser.new_page()
            log.info("Playwright: loading %s", _AUC_URL)
            page.goto(_AUC_URL, timeout=20000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("table", timeout=5000)
            except PWTimeout:
                log.warning("Playwright: tables did not appear — likely bot challenge")
                browser.close()
                return None
            html = page.content()
            browser.close()
            # Reject bot-challenge pages (no real table data)
            if html and ("TSPD" in html or "bobcmn" in html):
                log.warning("Playwright: got bot challenge page — skipping")
                return None
            log.info("Playwright: fetched %d bytes", len(html))
    except Exception as exc:
        log.debug("Playwright fetch failed: %s", exc)
    return html


def fetch_live_auction_rows(
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
) -> List[Dict]:
    """
    Fetch auction rows live from BB website.
    Falls back to CSV seed if live fetch fails.
    Merges both sources — live overrides CSV for same keys.
    """
    # ── Try live fetch: curl first (no Chrome needed), then Chrome, Playwright ──
    live_rows = []
    source_method = "CSV_ONLY"
    for method, fetcher in (("CURL", _fetch_with_curl),
                            ("CHROME", _fetch_with_chrome_f5),
                            ("PLAYWRIGHT", _fetch_with_playwright)):
        html = fetcher()
        if not html:
            continue
        try:
            live_rows = _parse_tables_from_html(html)
            if live_rows:
                source_method = method
                break
            log.warning("%s got HTML but parsed 0 rows", method)
        except Exception as exc:
            log.error("Live HTML parse error (%s): %s", method, exc)

    # ── Always load CSV as base ───────────────────────────────────────────────
    csv_rows = parse_auction_csv(date_from, date_to)

    # ── Merge: key = (auction_no, tenor_label, auction_date) ─────────────────
    if live_rows:
        # Build index from CSV
        merged = {}
        for r in csv_rows:
            k = (str(r.get("auction_no","")), r.get("tenor_label",""), r.get("auction_date",""))
            merged[k] = r

        new_count = updated = 0
        for r in live_rows:
            k = (str(r.get("auction_no","")), r.get("tenor_label",""), r.get("auction_date",""))
            if k in merged:
                updated += 1
            else:
                new_count += 1
            merged[k] = r

        all_rows = list(merged.values())
        if new_count:
            log.info("Live fetch found %d NEW auction rows not in CSV", new_count)
        log.info("Merged: %d total rows (live=%d, csv=%d, new=%d, updated=%d)",
                 len(all_rows), len(live_rows), len(csv_rows), new_count, updated)
    else:
        all_rows = csv_rows
        log.warning("Using CSV seed only (%d rows) — live calendar fetch failed", len(csv_rows))

    # ── Staleness alarm: fail LOUD, never silently serve an outdated calendar ──
    today_s = str(datetime.date.today())
    max_d = max((str(r["auction_date"])[:10] for r in all_rows), default=None)
    if max_d is None or max_d < today_s:
        log.error(
            "AUCTION CALENDAR STALE [source=%s]: newest known auction is %s (today %s). "
            "Forward auction outflows are MISSING, not zero — planned-outflow figures "
            "downstream are unreliable until BB's current-FY calendar is fetched.",
            source_method, max_d, today_s
        )

    # ── Date filter ───────────────────────────────────────────────────────────
    if date_from or date_to:
        df_str = date_from.isoformat()[:10] if date_from else None
        dt_str = date_to.isoformat()[:10]   if date_to   else None
        all_rows = [
            r for r in all_rows
            if (not df_str or str(r["auction_date"])[:10] >= df_str)
            and (not dt_str or str(r["auction_date"])[:10] <= dt_str)
        ]

    bills = [r for r in all_rows if r["security_type"] == "T_BILL"]
    bonds = [r for r in all_rows if r["security_type"] == "T_BOND"]
    frtbs = [r for r in all_rows if r["security_type"] == "FRTB"]
    log.info(
        "fetch_live_auction_rows [%s]: %d rows | T-Bill: %d | T-Bond: %d | FRTB: %d",
        source_method, len(all_rows), len(bills), len(bonds), len(frtbs)
    )
    return all_rows
