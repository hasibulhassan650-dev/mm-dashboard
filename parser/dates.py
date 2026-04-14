"""
parser/dates.py
Handles the two date formats observed on GSOM pages:
  - DD-MMM-YYYY   (bonds/FRTB)  e.g. 03-APR-2024
  - DD-MMM-YY     (T-Bills)     e.g. 29-DEC-25

All parsed dates are returned as datetime.date objects (never datetime).
Raw strings are always preserved alongside.
"""

import re
import logging
from datetime import date, datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Regex: matches both DD-MMM-YYYY and DD-MMM-YY
_DATE_RE = re.compile(
    r"^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "JAN": 1, "FEB": 2,  "MAR": 3,  "APR": 4,
    "MAY": 5, "JUN": 6,  "JUL": 7,  "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_gsom_date(raw: str) -> Tuple[Optional[date], str]:
    """
    Parse a GSOM date string into (date, raw_string).

    2-digit year disambiguation rule:
      00–68  → 2000–2068  (all Bangladesh govvies are post-2000)
      69–99  → 1969–1999  (would only apply to historical context)

    Returns (None, raw) and logs a warning on parse failure.
    Never raises — callers must check for None.
    """
    if not raw or not raw.strip():
        logger.warning("parse_gsom_date: empty input")
        return None, raw or ""

    raw = raw.strip()
    m = _DATE_RE.match(raw)
    if not m:
        logger.warning("parse_gsom_date: unrecognized format %r", raw)
        return None, raw

    day_s, mon_s, yr_s = m.group(1), m.group(2).upper(), m.group(3)

    month = _MONTH_MAP.get(mon_s)
    if month is None:
        logger.warning("parse_gsom_date: unknown month %r in %r", mon_s, raw)
        return None, raw

    day = int(day_s)
    yr_int = int(yr_s)

    if len(yr_s) == 2:
        # Python strptime %y behavior: 00–68 → 2000–2068
        year = 2000 + yr_int if yr_int <= 68 else 1900 + yr_int
    else:
        year = yr_int

    try:
        parsed = date(year, month, day)
    except ValueError as e:
        logger.warning("parse_gsom_date: invalid date %r: %s", raw, e)
        return None, raw

    return parsed, raw


def parse_gsom_date_strict(raw: str) -> date:
    """
    Same as parse_gsom_date but raises ValueError on failure.
    Use only where a None date would corrupt calculations.
    """
    result, original = parse_gsom_date(raw)
    if result is None:
        raise ValueError(f"Cannot parse GSOM date: {original!r}")
    return result


def date_to_str(d: date) -> str:
    """Canonical display format: DD-MMM-YYYY (e.g. 03-APR-2026)."""
    return d.strftime("%d-%b-%Y").upper()


def iso(d: date) -> str:
    """ISO 8601 string YYYY-MM-DD for storage and sorting."""
    return d.isoformat()


def parse_iso(s: str) -> date:
    """Parse ISO 8601 date string."""
    return date.fromisoformat(s)


def fiscal_year_for(d: date) -> str:
    """
    Return fiscal year label for a date.
    Bangladesh FY: 1 July – 30 June.
    e.g. 2026-04-09 → "2025-26"
         2025-07-01 → "2025-26"
         2025-06-30 → "2024-25"
    """
    if d.month >= 7:
        return f"{d.year}-{str(d.year + 1)[2:]}"
    else:
        return f"{d.year - 1}-{str(d.year)[2:]}"
