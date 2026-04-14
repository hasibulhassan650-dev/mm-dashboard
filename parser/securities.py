"""
parser/securities.py

Converts raw GSOM HTML into a list of normalized security dicts.
Each dict maps exactly to the securities + mtm_snapshots schema.

Column-position parsing (not header-text parsing) because:
  - T-Bond has duplicate "Securities" header in cols 2 and 3
  - Column headers have known typos ("Coupon Freqency")
  - Position is stable; header text is not guaranteed stable

Handles:
  - T-Bond  (15 columns, HFLY coupons)
  - FRTB    (15 columns, QRTY coupons, total row to skip)
  - T-Bill  (11 columns, no coupon columns)
"""

import logging
import re
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

from config import (
    SECURITY_TYPE_MAP, FREQ_MAP,
    INSTRUMENT_T_BILL, INSTRUMENT_T_BOND, INSTRUMENT_FRTB,
    FREQ_NONE, DQ_OK, DQ_MISSING_FIELD, DQ_MISSING_OUTSTANDING,
    GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL,
)
from parser.dates import parse_gsom_date, date_to_str

logger = logging.getLogger(__name__)

# ISIN pattern validation
_ISIN_RE = re.compile(r"^BD[A-Z0-9]{10}$")

# Thousands-separated number: "29,230.60" or "91,181.20"
_NUM_RE = re.compile(r"^[\d,]+\.?\d*$")


# ── Header extraction ──────────────────────────────────────────────────────────

def _parse_header(soup: BeautifulSoup) -> tuple[Optional[date], Optional[date]]:
    """
    Extract settlement_date and yield_date from the page H5 heading.

    H5 text example:
      "MTM Data for Settlement Date: 02-APR-2026"
    Yield date text (inside brackets below h5):
      "[Yield date: 01-APR-26]"

    Returns (settlement_date, yield_date). Both may be None on parse failure.
    """
    settlement_date: Optional[date] = None
    yield_date:      Optional[date] = None

    # Try h5 first
    h5 = soup.find("h5")
    if h5:
        text = h5.get_text(separator=" ").strip()
        m = re.search(r"Settlement Date[:\s]+(\d{1,2}-[A-Za-z]{3}-\d{2,4})", text)
        if m:
            settlement_date, _ = parse_gsom_date(m.group(1))
        else:
            logger.warning("parse_header: settlement date not found in h5: %r", text[:80])
    else:
        # Fallback: search full page text
        all_text = soup.get_text()
        m = re.search(r"Settlement Date[:\s]+(\d{1,2}-[A-Za-z]{3}-\d{2,4})", all_text)
        if m:
            settlement_date, _ = parse_gsom_date(m.group(1))
        else:
            logger.error("parse_header: settlement date not found anywhere in page")

    # Yield date (optional)
    all_text = soup.get_text()
    m = re.search(r"Yield date[:\s]+(\d{1,2}-[A-Za-z]{3}-\d{2,4})", all_text, re.IGNORECASE)
    if m:
        yield_date, _ = parse_gsom_date(m.group(1))

    return settlement_date, yield_date


# ── Number parsing ─────────────────────────────────────────────────────────────

def _parse_number(raw: str, field_name: str, isin: str) -> Optional[float]:
    """
    Strip commas and parse to float.
    Returns None and logs a warning if the field is blank or unparseable.
    Never defaults to 0.
    """
    s = raw.strip().replace(",", "")
    if not s or s == "-":
        logger.warning("Missing numeric field %r for ISIN %s (raw=%r)", field_name, isin, raw)
        return None
    try:
        return float(s)
    except ValueError:
        logger.warning("Unparseable numeric %r for %r ISIN %s", raw, field_name, isin)
        return None


# ── Security name normalization ────────────────────────────────────────────────

def _normalize_name(raw: str) -> str:
    """
    PR-1: Normalize security name to consistent format.
    "02 Year BGTB 05/02/2027" → "02Y BGTB 05/02/2027"
    "2Y BGTB 07/08/2026"      → "02Y BGTB 07/08/2026"
    """
    s = raw.strip()
    # "02 Year" / "2 Year" → "02Y" / "2Y"
    s = re.sub(r"(\d+)\s+[Yy]ear\b", lambda m: f"{int(m.group(1)):02d}Y", s)
    # Zero-pad bare "2Y" → "02Y" at start of string
    s = re.sub(r"^(\d)Y\b", lambda m: f"0{m.group(1)}Y", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s)
    return s


# ── Row validation ─────────────────────────────────────────────────────────────

def _is_data_row(cells: list, source_type: str) -> bool:
    """
    Return True if a table row is a valid security data row.
    Skips: header rows, total rows, empty rows.
    """
    if len(cells) < 5:
        return False

    # Col 1 (ISIN) must match pattern
    isin_candidate = cells[1].get_text(separator=" ").strip()
    if not _ISIN_RE.match(isin_candidate):
        # Could be a total row (FRTB) or header repeat — skip silently
        if "total" in " ".join(c.get_text() for c in cells).lower():
            logger.debug("Skipping total row")
        return False

    return True


# ── Data quality ──────────────────────────────────────────────────────────────

def _compute_dq(rec: dict) -> str:
    flags = []
    if rec.get("outstanding_bdt_mill") is None:
        flags.append(DQ_MISSING_OUTSTANDING)
    for f in ("isin", "maturity_date", "issue_date"):
        if rec.get(f) is None:
            flags.append(f"{DQ_MISSING_FIELD}:{f}")
    return ",".join(flags) if flags else DQ_OK


# ── Per-page parsers ───────────────────────────────────────────────────────────

def _parse_bond_row(cells: list, settlement_date: date, source_url: str, row_idx: int) -> Optional[dict]:
    """
    Parse a single T-Bond or FRTB table row (15 columns).

    Column index map:
      0  Sl.No
      1  ISIN
      2  Security name
      3  Security type label
      4  Issue Date
      5  Maturity/Expiry Date
      6  Coupon Rate
      7  Coupon Frequency
      8  Last Coupon Date
      9  Next Coupon Date
      10 Issue Price
      11 Remaining Maturity
      12 Market Yield
      13 Market Price
      14 Outstanding BDT (in Mill)
    """
    def txt(i):
        return cells[i].get_text(separator=" ").strip() if i < len(cells) else ""

    isin = txt(1)
    if not _ISIN_RE.match(isin):
        return None

    name_raw  = txt(2)
    type_raw  = txt(3)

    issue_date_raw    = txt(4)
    maturity_date_raw = txt(5)
    coupon_rate_raw   = txt(6)
    freq_raw          = txt(7)
    last_coup_raw     = txt(8)
    next_coup_raw     = txt(9)
    issue_price_raw   = txt(10)
    rem_mat_raw       = txt(11)
    mkt_yield_raw     = txt(12)
    mkt_price_raw     = txt(13)
    outstanding_raw   = txt(14)

    issue_date,    _ = parse_gsom_date(issue_date_raw)
    maturity_date, _ = parse_gsom_date(maturity_date_raw)
    last_coup_date, _ = parse_gsom_date(last_coup_raw) if last_coup_raw else (None, "")
    next_coup_date, _ = parse_gsom_date(next_coup_raw) if next_coup_raw else (None, "")

    security_type = SECURITY_TYPE_MAP.get(type_raw)
    if security_type is None:
        logger.warning("Unknown security type %r for ISIN %s", type_raw, isin)
        security_type = "UNKNOWN"

    coupon_frequency = FREQ_MAP.get(freq_raw.upper(), "UNKNOWN")

    rec = {
        # Identity
        "isin":                    isin,
        "security_name_raw":       name_raw,
        "security_name_normalized": _normalize_name(name_raw),
        "security_type":           security_type,
        # Dates — raw and parsed
        "issue_date_raw":          issue_date_raw,
        "issue_date":              issue_date,
        "maturity_date_raw":       maturity_date_raw,
        "maturity_date":           maturity_date,
        "last_coupon_date_raw":    last_coup_raw,
        "last_coupon_date":        last_coup_date,
        "next_coupon_date_raw":    next_coup_raw,
        "next_coupon_date":        next_coup_date,
        # Coupon
        "coupon_rate_pct":         _parse_number(coupon_rate_raw, "coupon_rate", isin),
        "coupon_frequency":        coupon_frequency,
        # Pricing
        "issue_price":             _parse_number(issue_price_raw, "issue_price", isin),
        "outstanding_bdt_mill":    _parse_number(outstanding_raw, "outstanding", isin),
        "market_yield_pct":        _parse_number(mkt_yield_raw, "market_yield", isin),
        "market_price":            _parse_number(mkt_price_raw, "market_price", isin),
        # Remaining maturity — bonds/FRTB are in YEARS
        "remaining_maturity_raw":   rem_mat_raw,
        "remaining_maturity_value": _parse_number(rem_mat_raw, "remaining_maturity", isin),
        "remaining_maturity_unit":  "YEARS",
        # Snapshot provenance
        "settlement_date":         settlement_date,
        "source_page":             source_url,
        "source_row_index":        row_idx,
        # Raw for audit
        "outstanding_raw":         outstanding_raw,
    }

    rec["data_quality"] = _compute_dq(rec)
    return rec


def _parse_bill_row(cells: list, settlement_date: date, source_url: str, row_idx: int) -> Optional[dict]:
    """
    Parse a single T-Bill table row (11 columns — no coupon columns).

    Column index map:
      0  Sl.No
      1  ISIN
      2  Security name
      3  Security type label
      4  Issue Date
      5  Maturity/Expiry Date
      6  Issue Price
      7  Remaining Maturity  (DAYS, not years)
      8  Market Yield
      9  Market Price
      10 Outstanding BDT (in Mill)
    """
    def txt(i):
        return cells[i].get_text(separator=" ").strip() if i < len(cells) else ""

    isin = txt(1)
    if not _ISIN_RE.match(isin):
        return None

    name_raw          = txt(2)
    type_raw          = txt(3)
    issue_date_raw    = txt(4)
    maturity_date_raw = txt(5)
    issue_price_raw   = txt(6)
    rem_mat_raw       = txt(7)
    mkt_yield_raw     = txt(8)
    mkt_price_raw     = txt(9)
    outstanding_raw   = txt(10)

    issue_date,    _ = parse_gsom_date(issue_date_raw)
    maturity_date, _ = parse_gsom_date(maturity_date_raw)

    security_type = SECURITY_TYPE_MAP.get(type_raw, INSTRUMENT_T_BILL)

    rec = {
        "isin":                    isin,
        "security_name_raw":       name_raw,
        "security_name_normalized": _normalize_name(name_raw),
        "security_type":           security_type,
        "issue_date_raw":          issue_date_raw,
        "issue_date":              issue_date,
        "maturity_date_raw":       maturity_date_raw,
        "maturity_date":           maturity_date,
        # Coupon — structurally absent for T-Bills
        "last_coupon_date_raw":    None,
        "last_coupon_date":        None,
        "next_coupon_date_raw":    None,
        "next_coupon_date":        None,
        "coupon_rate_pct":         None,
        "coupon_frequency":        FREQ_NONE,
        # Pricing
        "issue_price":             _parse_number(issue_price_raw, "issue_price", isin),
        "outstanding_bdt_mill":    _parse_number(outstanding_raw, "outstanding", isin),
        "market_yield_pct":        _parse_number(mkt_yield_raw, "market_yield", isin),
        "market_price":            _parse_number(mkt_price_raw, "market_price", isin),
        # Remaining maturity — T-Bills are in DAYS
        "remaining_maturity_raw":   rem_mat_raw,
        "remaining_maturity_value": _parse_number(rem_mat_raw, "remaining_maturity", isin),
        "remaining_maturity_unit":  "DAYS",
        # Snapshot provenance
        "settlement_date":         settlement_date,
        "source_page":             source_url,
        "source_row_index":        row_idx,
        "outstanding_raw":         outstanding_raw,
    }

    rec["data_quality"] = _compute_dq(rec)
    return rec


# ── Main parse functions ───────────────────────────────────────────────────────

def parse_gsom_html(html: str, source_url: str) -> list[dict]:
    """
    Parse a GSOM page HTML string. Auto-detects page type from source_url.
    Returns list of normalized security dicts.

    Raises ParseError if settlement_date cannot be extracted (mandatory for all rows).
    """
    soup = BeautifulSoup(html, "lxml")

    settlement_date, yield_date = _parse_header(soup)

    if settlement_date is None:
        raise ParseError(
            f"Cannot parse settlement date from {source_url}. "
            "Aborting — all rows require a settlement date."
        )

    logger.info(
        "Parsing %s | settlement=%s | yield_date=%s",
        source_url, settlement_date, yield_date,
    )

    is_tbill = (source_url == GSOM_TBILL_URL or "bill" in source_url.lower())

    # Find the main data table — the one with the most rows
    tables = soup.find_all("table")
    if not tables:
        raise ParseError(f"No tables found in {source_url}")

    data_table = max(tables, key=lambda t: len(t.find_all("tr")))
    rows = data_table.find_all("tr")

    records = []
    for row_idx, row in enumerate(rows):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        if not _is_data_row(cells, source_url):
            continue

        if is_tbill:
            rec = _parse_bill_row(cells, settlement_date, source_url, row_idx)
        else:
            rec = _parse_bond_row(cells, settlement_date, source_url, row_idx)

        if rec:
            rec["yield_date"] = yield_date
            records.append(rec)

    logger.info("Parsed %d securities from %s", len(records), source_url)

    # Validate ISIN uniqueness within this snapshot
    seen = {}
    unique_records = []
    for r in records:
        key = (r["isin"], r["settlement_date"])
        if key in seen:
            logger.warning(
                "Duplicate ISIN %s on settlement_date %s — keeping first occurrence",
                r["isin"], r["settlement_date"],
            )
        else:
            seen[key] = True
            unique_records.append(r)

    return unique_records


class ParseError(Exception):
    """Raised when a mandatory parse step fails."""
    pass
