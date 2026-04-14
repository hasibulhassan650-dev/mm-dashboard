"""
fetchers/auction_parser.py — Parse BOTH Treasury Bill and Treasury Bond
auction calendar sections from the BB auction calendar page.

SOURCE: https://www.bb.org.bd/en/index.php/monetaryactivity/auc_calendar

PAGE STRUCTURE (two separate sections):
  Section 1: "Treasury Bills Auction Calendar of FY20XX-XX"
    Columns: Auction no | Auction Date | 14 days | 91 days | 182 days | 364 days | Total

  Section 2: "Treasury Bonds (BGTBs) Auction Calendar of FY20XX-XX"
    Columns: Auction no | Auction Date | 2Y | 5Y | 10Y | 15Y | 20Y | Total

ACCESS: The page is JS-gated. This parser handles two input modes:
  Mode A — HTML string (from browser automation / Playwright / saved page)
  Mode B — CSV seed file (manual fallback, always available)

Both modes produce identical output row format.

OUTPUT ROW FORMAT (one row per tenor per auction date):
  {
    source_section:        "Treasury Bills" | "Treasury Bonds"
    fiscal_year:           "2025-26"
    auction_no:            44
    auction_date:          datetime.date
    instrument_type:       "BILL" | "BOND" | "FRTB"
    security_type:         "T_BILL" | "T_BOND" | "FRTB"
    tenor_label:           "91D" | "182D" | "364D" | "2Y" | "5Y" | ...
    offered_bdt_crore_raw: 3500.0          ← raw from source
    offered_amount_bdt_crore: 3500.0       ← same, kept for traceability
    offered_amount_bdt_mill:  35000.0      ← crore × 10
    row_total_bdt_crore:   9000.0          ← total column from source row
    row_total_bdt_mill:    90000.0
    outflow_status:        "PLANNED"
    source:                URL or file path
    source_row_num:        int
  }
"""
import re
import logging
import datetime
import csv
from io import StringIO
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from config import SEEDS_DIR, CRORE_TO_MILLION

log = logging.getLogger(__name__)

AUCTION_SEED = SEEDS_DIR / "auction_calendar.csv"

# ── Tenor normalisation ────────────────────────────────────────────────────────
# Maps column header text → (tenor_label, instrument_type, security_type)
_BILL_TENOR_MAP = {
    "14":    ("14D",  "BILL", "T_BILL"),
    "14d":   ("14D",  "BILL", "T_BILL"),
    "14day": ("14D",  "BILL", "T_BILL"),
    "91":    ("91D",  "BILL", "T_BILL"),
    "91d":   ("91D",  "BILL", "T_BILL"),
    "91day": ("91D",  "BILL", "T_BILL"),
    "182":   ("182D", "BILL", "T_BILL"),
    "182d":  ("182D", "BILL", "T_BILL"),
    "364":   ("364D", "BILL", "T_BILL"),
    "364d":  ("364D", "BILL", "T_BILL"),
}

_BOND_TENOR_MAP = {
    "2":    ("2Y",  "BOND", "T_BOND"),
    "2y":   ("2Y",  "BOND", "T_BOND"),
    "2yr":  ("2Y",  "BOND", "T_BOND"),
    "5":    ("5Y",  "BOND", "T_BOND"),
    "5y":   ("5Y",  "BOND", "T_BOND"),
    "10":   ("10Y", "BOND", "T_BOND"),
    "10y":  ("10Y", "BOND", "T_BOND"),
    "15":   ("15Y", "BOND", "T_BOND"),
    "15y":  ("15Y", "BOND", "T_BOND"),
    "20":   ("20Y", "BOND", "T_BOND"),
    "20y":  ("20Y", "BOND", "T_BOND"),
    "3":    ("3Y",  "BOND", "FRTB"),   # 3Y FRTB
    "3y":   ("3Y",  "BOND", "FRTB"),
    "frtb": ("3Y",  "BOND", "FRTB"),
}


def _clean_amount(s: str) -> Optional[float]:
    """Strip commas/spaces, return float or None for blank/dash cells."""
    if not s:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "N/A", "n/a", "0.00", "0"):
        return None     # treat 0 as None so we skip zero-offer tenors
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def _parse_date(s: str) -> Optional[datetime.date]:
    """Parse auction date strings: 'DD-MMM-YYYY', 'YYYY-MM-DD', 'DD/MM/YYYY'."""
    s = s.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    log.warning("Could not parse auction date: '%s'", s)
    return None


def _make_row(
    section: str,
    fiscal_year: str,
    auction_no,
    auction_date: datetime.date,
    tenor_label: str,
    instrument_type: str,
    security_type: str,
    offered_crore: float,
    row_total_crore: float,
    source: str,
    source_row_num: int,
) -> Dict:
    return {
        "source_section":           section,
        "fiscal_year":              fiscal_year,
        "auction_no":               auction_no,
        "auction_date":             auction_date,
        "instrument_type":          instrument_type,
        "security_type":            security_type,
        "tenor_label":              tenor_label,
        "offered_bdt_crore_raw":    offered_crore,
        "offered_amount_bdt_crore": offered_crore,
        "offered_amount_bdt_mill":  round(offered_crore * CRORE_TO_MILLION, 4),
        "row_total_bdt_crore":      row_total_crore,
        "row_total_bdt_mill":       round(row_total_crore * CRORE_TO_MILLION, 4),
        "accepted_amount_bdt_crore": None,
        "accepted_amount_bdt_mill":  None,
        "outflow_status":           "PLANNED",
        "source":                   source,
        "source_row_num":           source_row_num,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MODE A — Parse HTML from the BB auction calendar page
# ══════════════════════════════════════════════════════════════════════════════

def parse_auction_html(html: str, source_url: str) -> List[Dict]:
    """
    Parse raw HTML from the BB auction calendar page.
    Detects BOTH the Treasury Bills section and the Treasury Bonds section.
    Returns a flat list of per-tenor rows from both sections.

    This function does NOT fetch the page — call it with HTML you already have
    (from Playwright, a saved .html file, or browser dev-tools copy-paste).
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.error("beautifulsoup4 not installed — cannot parse HTML")
        return []

    soup = BeautifulSoup(html, "lxml")
    rows = []

    # ── Find all section headings and their associated tables ─────────────────
    # BB page uses h4/h5/h6 or strong tags with section names like:
    # "Treasury Bills Auction Calendar of FY2025-2026"
    # "Treasury Bonds (BGTBs) Auction Calendar of FY2025-2026"

    # Strategy: find all tables; identify each table's section by looking at
    # the nearest preceding heading element.
    tables = soup.find_all("table")
    log.info("HTML parse: found %d tables on page", len(tables))

    for table_idx, table in enumerate(tables):
        # Walk backward through siblings/parents to find section heading
        section_name = _detect_section(table)
        if section_name is None:
            log.debug("Table %d: no recognisable section heading — skipping", table_idx)
            continue

        is_bill = "bill" in section_name.lower()
        is_bond = "bond" in section_name.lower() or "bgtb" in section_name.lower()

        if not is_bill and not is_bond:
            log.debug("Table %d: unrecognised section '%s' — skipping", table_idx, section_name)
            continue

        tenor_map = _BILL_TENOR_MAP if is_bill else _BOND_TENOR_MAP
        section_label = "Treasury Bills" if is_bill else "Treasury Bonds"

        # Extract fiscal year from section heading
        fy = _extract_fiscal_year(section_name)

        # Parse header row to find column positions
        headers = _extract_headers(table)
        if not headers:
            log.warning("Table %d (%s): no header row found", table_idx, section_label)
            continue

        # Map column index → (tenor_label, instrument_type, security_type)
        col_map = {}   # col_index → tenor info
        total_col = None
        date_col  = None
        no_col    = None

        for i, h in enumerate(headers):
            # Normalise header: remove spaces, handle "days" and "day" both
            h_lower = (h.strip().lower()
                        .replace(" ", "")
                        .replace("-", "")
                        .replace("days", "d")   # plural first
                        .replace("day", "d"))
            if h_lower in ("auctionno", "no", "slno", "sl"):
                no_col = i
            elif h_lower in ("auctiondate", "date"):
                date_col = i
            elif h_lower in ("total", "totalamount", "amount"):
                total_col = i
            elif h_lower in tenor_map:
                col_map[i] = tenor_map[h_lower]
            else:
                # Try stripping trailing chars and re-matching
                h_stripped = re.sub(r"[^0-9a-z]", "", h_lower)
                if h_stripped in tenor_map:
                    col_map[i] = tenor_map[h_stripped]

        if date_col is None:
            log.warning("Table %d (%s): cannot find date column in headers %s",
                        table_idx, section_label, headers)
            continue

        log.info("Table %d — Section: %s | FY: %s | Tenor cols: %d",
                 table_idx, section_label, fy, len(col_map))

        # Parse data rows
        data_rows = table.find_all("tr")[1:]   # skip header
        for row_num, tr in enumerate(data_rows, start=2):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < max(date_col + 1, 2):
                continue

            # Auction date
            raw_date = cells[date_col] if date_col < len(cells) else ""
            adate = _parse_date(raw_date)
            if adate is None:
                continue   # skip header-repeat and total rows

            # Auction number
            ano = None
            if no_col is not None and no_col < len(cells):
                try:
                    ano = int(cells[no_col].strip())
                except (ValueError, TypeError):
                    ano = cells[no_col].strip() or None

            # Total amount from total column
            total_crore = 0.0
            if total_col is not None and total_col < len(cells):
                total_crore = _clean_amount(cells[total_col]) or 0.0

            # Per-tenor amounts
            tenor_rows_added = 0
            for col_i, (tenor_label, inst_type, sec_type) in col_map.items():
                if col_i >= len(cells):
                    continue
                amt = _clean_amount(cells[col_i])
                if amt is None:
                    continue   # skip zero / blank tenor for this row

                # Recompute total if not in a dedicated column
                if total_crore == 0:
                    total_crore = sum(
                        _clean_amount(cells[ci]) or 0.0
                        for ci in col_map
                        if ci < len(cells)
                    )

                rows.append(_make_row(
                    section=section_label,
                    fiscal_year=fy,
                    auction_no=ano,
                    auction_date=adate,
                    tenor_label=tenor_label,
                    instrument_type=inst_type,
                    security_type=sec_type,
                    offered_crore=amt,
                    row_total_crore=total_crore,
                    source=source_url,
                    source_row_num=row_num,
                ))
                tenor_rows_added += 1

            if tenor_rows_added == 0:
                log.debug("Row %d date=%s: all tenor amounts zero/blank — skipped", row_num, adate)

    _log_parse_summary(rows, source_url)
    return rows


def _detect_section(table) -> Optional[str]:
    """Walk backward from the table to find the nearest section heading."""
    # Check previous siblings
    for sibling in table.previous_siblings:
        text = getattr(sibling, "get_text", lambda **k: "")(" ", strip=True)
        if any(kw in text.lower() for kw in ("treasury bill", "treasury bond", "bgtb")):
            return text
    # Check parent's previous siblings
    if table.parent:
        for sibling in table.parent.previous_siblings:
            text = getattr(sibling, "get_text", lambda **k: "")(" ", strip=True)
            if any(kw in text.lower() for kw in ("treasury bill", "treasury bond", "bgtb")):
                return text
    # Check for caption inside table
    caption = table.find("caption")
    if caption:
        return caption.get_text(" ", strip=True)
    return None


def _extract_fiscal_year(heading: str) -> str:
    """Extract 'YYYY-YY' from heading text like 'FY2025-2026' or 'FY2025-26'."""
    m = re.search(r"FY\s*(\d{4})[–\-](\d{2,4})", heading, re.I)
    if m:
        y1 = m.group(1)
        y2 = m.group(2)
        if len(y2) == 4:
            y2 = y2[-2:]
        return f"{y1}-{y2}"
    return "UNKNOWN"


def _extract_headers(table) -> List[str]:
    """Extract text from the first <tr> of the table."""
    first_row = table.find("tr")
    if not first_row:
        return []
    return [th.get_text(" ", strip=True) for th in first_row.find_all(["th", "td"])]


def _log_parse_summary(rows: List[Dict], source: str) -> None:
    bill_rows = [r for r in rows if r["security_type"] == "T_BILL"]
    bond_rows = [r for r in rows if r["security_type"] == "T_BOND"]
    frtb_rows = [r for r in rows if r["security_type"] == "FRTB"]
    log.info(
        "Auction HTML parse from %s: "
        "%d total rows | T-Bill: %d | T-Bond: %d | FRTB: %d",
        source, len(rows), len(bill_rows), len(bond_rows), len(frtb_rows)
    )


# ══════════════════════════════════════════════════════════════════════════════
# MODE B — Read from CSV seed file (always-available fallback)
# ══════════════════════════════════════════════════════════════════════════════

def parse_auction_csv(
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
) -> List[Dict]:
    """
    Read auction rows from the seed CSV file.
    Handles both T-Bill and T-Bond rows identically.
    Skips rows with zero offered amounts.
    """
    if not AUCTION_SEED.exists():
        log.error(
            "Auction seed CSV not found: %s\n"
            "Create it with columns matching the BB auction calendar.\n"
            "Both T-Bill and T-Bond rows must be included.",
            AUCTION_SEED,
        )
        return []

    rows = []
    with open(AUCTION_SEED, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_num, r in enumerate(reader, start=2):
            # Normalise keys: strip whitespace
            r = {k.strip(): v.strip() for k, v in r.items() if k}

            # auction_date
            raw_date = r.get("auction_date", "")
            adate = _parse_date(raw_date)
            if adate is None:
                log.warning("CSV row %d: bad auction_date '%s' — skipped", row_num, raw_date)
                continue

            # Date range filter
            if date_from and adate < date_from:
                continue
            if date_to and adate > date_to:
                continue

            # offered_bdt_crore
            offered_crore = _clean_amount(r.get("offered_bdt_crore", ""))
            if offered_crore is None:
                log.debug("CSV row %d: zero/blank offered amount — skipped", row_num)
                continue

            # Security / instrument type
            security_type  = r.get("security_type", "").strip().upper()
            instrument_type = r.get("instrument_type", "").strip().upper()

            # Derive instrument_type from security_type if missing
            if not instrument_type:
                instrument_type = (
                    "BILL" if security_type == "T_BILL"
                    else "BOND"
                )

            # Section label
            section = (
                "Treasury Bills" if security_type == "T_BILL"
                else "Treasury Bonds"
            )

            # Fiscal year
            fy = r.get("fiscal_year", "").strip()
            if not fy:
                from config import fiscal_year as get_fy
                fy = get_fy(adate)

            # Auction number
            ano_raw = r.get("auction_no", "").strip()
            try:
                ano = int(ano_raw)
            except (ValueError, TypeError):
                ano = ano_raw or None

            # Accepted amount
            acc_crore_raw = r.get("accepted_bdt_crore", "").strip()
            acc_crore = _clean_amount(acc_crore_raw)

            # Status
            status = (r.get("outflow_status", "") or "PLANNED").strip().upper() or "PLANNED"
            if acc_crore is not None and status != "CONFIRMED":
                status = "CONFIRMED"

            # Yield
            wyld_raw = r.get("weighted_avg_yield_pct", "").strip()
            try:
                wyld = float(wyld_raw) if wyld_raw else None
            except ValueError:
                wyld = None

            row_dict = _make_row(
                section=section,
                fiscal_year=fy,
                auction_no=ano,
                auction_date=adate,
                tenor_label=r.get("tenor_label", "UNKNOWN").strip(),
                instrument_type=instrument_type,
                security_type=security_type,
                offered_crore=offered_crore,
                row_total_crore=offered_crore,   # seed CSV has one row per tenor
                source=str(AUCTION_SEED),
                source_row_num=row_num,
            )
            # Overlay accepted and yield
            if acc_crore is not None:
                row_dict["accepted_amount_bdt_crore"] = acc_crore
                row_dict["accepted_amount_bdt_mill"]  = round(acc_crore * CRORE_TO_MILLION, 4)
            row_dict["outflow_status"] = status
            row_dict["weighted_avg_yield_pct"] = wyld
            row_dict["resulting_isin"] = r.get("resulting_isin", "").strip() or None

            rows.append(row_dict)

    _log_parse_summary(rows, str(AUCTION_SEED))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED FETCH ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def fetch_auction_rows(
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
    html: str = None,
    source_url: str = None,
) -> List[Dict]:
    """
    Main entry point. Returns rows from BOTH Treasury Bills and Treasury Bonds.

    Priority:
      1. If `html` is provided → parse it (Mode A).
         Use this when you have the page HTML from Playwright or a saved file.
      2. Otherwise → read from CSV seed (Mode B, always available).

    The return value is always a flat list ready for generate_all_auction_events().
    """
    if html:
        rows = parse_auction_html(html, source_url or "PROVIDED_HTML")
        if rows:
            return rows
        log.warning("HTML parse returned 0 rows — falling back to CSV seed")

    return parse_auction_csv(date_from, date_to)
