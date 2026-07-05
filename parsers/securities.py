"""
parsers/securities.py — Parse GSOM HTML into normalised security dicts.

Column positions (0-indexed), confirmed from live page inspection:
  T_BOND / FRTB (15 cols):
    0:Sl  1:ISIN  2:Name  3:Type  4:IssueDate  5:MatDate
    6:CouponRate  7:CouponFreq  8:LastCoupon  9:NextCoupon
    10:IssuePrice  11:RemMat  12:Yield  13:Price  14:Outstanding
  T_BILL (11 cols):
    0:Sl  1:ISIN  2:Name  3:Type  4:IssueDate  5:MatDate
    6:IssuePrice  7:RemMat  8:Yield  9:Price  10:Outstanding
"""
import re
import logging
import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

from calendar_utils import parse_gsom_date
from config import GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL

log = logging.getLogger(__name__)

ISIN_PATTERN = re.compile(r"^BD[A-Z0-9]{10}$")
_TYPE_MAP = {
    "treasury bond": "T_BOND",
    "frtb":          "FRTB",
    "treasury bill": "T_BILL",
}
_FREQ_MAP = {
    "hfly": "HFLY",
    "qrty": "QRTY",
}


def _clean_num(s: str) -> Optional[float]:
    """Strip commas, whitespace, parse float. Returns None on failure."""
    if not s:
        return None
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _norm_name(raw: str) -> str:
    """Normalize security name to canonical form."""
    s = raw.strip()
    s = re.sub(r"\b(\d+)\s+[Yy]ear\b", r"\1Y", s)
    # zero-pad tenor: '2Y' -> '02Y', '5Y' -> '05Y'
    s = re.sub(r"\b(\d)Y\b", lambda m: f"0{m.group(1)}Y", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _parse_header(soup: BeautifulSoup) -> Dict:
    """
    Extract settlement date and yield date from the page header.
    Searches h4, h5, h6, and paragraph tags to be robust against
    minor page layout changes by BB.
    """
    header = {"settlement_date": None, "yield_date": None}

    # Collect all candidate text blocks from heading/para elements
    candidates = []
    for tag in soup.find_all(["h4", "h5", "h6", "p", "div"]):
        text = tag.get_text(" ", strip=True)
        if "settlement" in text.lower() or "date" in text.lower():
            candidates.append(text)

    # Also search the full page text as fallback
    full_text = soup.get_text(" ")
    candidates.append(full_text)

    for text in candidates:
        if header["settlement_date"] is None:
            m = re.search(r"Settlement\s+Date[:\s]+(\d{2}-[A-Z]{3}-\d{2,4})", text, re.I)
            if m:
                header["settlement_date"] = parse_gsom_date(m.group(1))
        if header["yield_date"] is None:
            m = re.search(r"Yield\s+date[:\s]+(\d{2}-[A-Z]{3}-\d{2,4})", text, re.I)
            if m:
                header["yield_date"] = parse_gsom_date(m.group(1))
        if header["settlement_date"] is not None:
            break

    if header["settlement_date"] is None:
        log.error("Could not find Settlement Date in page — will use today as fallback")
        header["settlement_date"] = datetime.date.today()

    return header


def _quality(row: dict) -> str:
    flags = []
    if row.get("outstanding_bdt_mill") is None:
        flags.append("MISSING_OUTSTANDING")
    if row.get("maturity_date") is None:
        flags.append("MISSING_MATURITY_DATE")
    if row["security_type"] in ("T_BOND", "FRTB"):
        if row.get("coupon_rate_pct") is None:
            flags.append("MISSING_COUPON_RATE")
        if row.get("next_coupon_date") is None:
            flags.append("MISSING_NEXT_COUPON")
    return ",".join(flags) if flags else "OK"


def parse_tbond_or_frtb(html: str, source_url: str, security_type: str) -> List[Dict]:
    """Parse T-Bond or FRTB MTM HTML. Returns list of row dicts."""
    soup = BeautifulSoup(html, "lxml")
    header = _parse_header(soup)
    if header["settlement_date"] is None:
        raise ValueError(f"Aborted parse of {source_url}: settlement date missing from header")

    table = soup.find("table")
    if not table:
        raise ValueError(f"No table found in {source_url}")

    rows = []
    for tr in table.find_all("tr")[1:]:   # skip header row
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 15:
            continue
        isin = cells[1].strip()
        if not ISIN_PATTERN.match(isin):
            # Total row or junk — skip
            log.debug("Skipping non-ISIN row: %s", cells[:3])
            continue

        raw_type = cells[3].strip().lower()
        stype = _TYPE_MAP.get(raw_type, security_type)

        row = {
            "source_row_index":       _clean_num(cells[0]),
            "isin":                   isin,
            "security_name_raw":      cells[2].strip(),
            "security_name_norm":     _norm_name(cells[2]),
            "security_type":          stype,
            "issue_date_raw":         cells[4].strip(),
            "issue_date":             parse_gsom_date(cells[4]),
            "maturity_date_raw":      cells[5].strip(),
            "maturity_date":          parse_gsom_date(cells[5]),
            "coupon_rate_pct":        _clean_num(cells[6]),
            "coupon_frequency":       _FREQ_MAP.get(cells[7].strip().lower(), "UNKNOWN"),
            "last_coupon_date_raw":   cells[8].strip(),
            "last_coupon_date":       parse_gsom_date(cells[8]),
            "next_coupon_date_raw":   cells[9].strip(),
            "next_coupon_date":       parse_gsom_date(cells[9]),
            "issue_price":            _clean_num(cells[10]),
            "remaining_maturity_raw": cells[11].strip(),
            "remaining_maturity_val": _clean_num(cells[11]),
            "remaining_maturity_unit": "YEARS",
            "market_yield_pct":       _clean_num(cells[12]),
            "market_price":           _clean_num(cells[13]),
            "outstanding_bdt_mill":   _clean_num(cells[14]),
            "settlement_date":        header["settlement_date"],
            "yield_date":             header["yield_date"],
            "source_page":            source_url,
        }
        row["data_quality"] = _quality(row)
        rows.append(row)

    log.info("Parsed %d rows from %s", len(rows), source_url)
    return rows


def parse_tbill(html: str, source_url: str) -> List[Dict]:
    """Parse T-Bill MTM HTML (11-column table, no coupon columns)."""
    soup = BeautifulSoup(html, "lxml")
    header = _parse_header(soup)
    if header["settlement_date"] is None:
        raise ValueError(f"Aborted parse of {source_url}: settlement date missing")

    table = soup.find("table")
    if not table:
        raise ValueError(f"No table in {source_url}")

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 11:
            continue
        isin = cells[1].strip()
        if not ISIN_PATTERN.match(isin):
            continue

        row = {
            "source_row_index":       _clean_num(cells[0]),
            "isin":                   isin,
            "security_name_raw":      cells[2].strip(),
            "security_name_norm":     _norm_name(cells[2]),
            "security_type":          "T_BILL",
            "issue_date_raw":         cells[4].strip(),
            "issue_date":             parse_gsom_date(cells[4]),
            "maturity_date_raw":      cells[5].strip(),
            "maturity_date":          parse_gsom_date(cells[5]),
            "coupon_rate_pct":        None,    # T-Bills have no coupon
            "coupon_frequency":       "NONE",
            "last_coupon_date_raw":   None,
            "last_coupon_date":       None,
            "next_coupon_date_raw":   None,
            "next_coupon_date":       None,
            "issue_price":            _clean_num(cells[6]),
            "remaining_maturity_raw": cells[7].strip(),
            "remaining_maturity_val": _clean_num(cells[7]),
            "remaining_maturity_unit": "DAYS",   # T-Bill rem. mat. is in days
            "market_yield_pct":       _clean_num(cells[8]),
            "market_price":           _clean_num(cells[9]),
            "outstanding_bdt_mill":   _clean_num(cells[10]),
            "settlement_date":        header["settlement_date"],
            "yield_date":             header["yield_date"],
            "source_page":            source_url,
        }
        row["data_quality"] = _quality(row)
        rows.append(row)

    log.info("Parsed %d T-Bill rows from %s", len(rows), source_url)
    return rows


def validate_rows(rows: List[Dict]) -> List[Dict]:
    """Run cross-row validations. Returns rows with updated data_quality."""
    isins_seen = {}
    for row in rows:
        isin = row["isin"]
        sdate = row.get("settlement_date")

        # V1: ISIN format
        if not ISIN_PATTERN.match(isin):
            row["data_quality"] += ",INVALID_ISIN"

        # V2: duplicate ISIN on same settlement date
        key = (isin, sdate)
        if key in isins_seen:
            log.warning("Duplicate ISIN %s on %s — keeping first", isin, sdate)
            row["data_quality"] += ",DUPLICATE_ISIN"
        else:
            isins_seen[key] = True

        # V4: maturity > issue
        if row.get("maturity_date") and row.get("issue_date"):
            if row["maturity_date"] <= row["issue_date"]:
                row["data_quality"] += ",MATURITY_BEFORE_ISSUE"

        # V6: coupon rate range
        cr = row.get("coupon_rate_pct")
        if cr is not None and (cr <= 0 or cr > 30):
            row["data_quality"] += ",SUSPECT_COUPON_RATE"

        # V8: coupon date gap
        lc = row.get("last_coupon_date")
        nc = row.get("next_coupon_date")
        freq = row.get("coupon_frequency")
        if lc and nc and freq in ("HFLY", "QRTY"):
            gap = (nc - lc).days
            expected = 182 if freq == "HFLY" else 91
            if abs(gap - expected) > 5:
                row["data_quality"] += f",COUPON_GAP_ANOMALY({gap}d)"

    return rows
