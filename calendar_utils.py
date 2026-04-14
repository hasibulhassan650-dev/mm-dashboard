"""
calendar_utils.py — Working day logic for Bangladesh money market.

STRICT RULES (as specified):
  Working days  = Sunday, Monday, Tuesday, Wednesday, Thursday
  Non-working   = Friday (weekday 4), Saturday (weekday 5)
  Non-working   = Any date in the provided holiday list

AUCTION OUTFLOW SETTLEMENT (strict T+1 next-working-day):
  Step 1: Take auction_date
  Step 2: Move to next calendar day  (auction_date + 1)
  Step 3: Check that day:
            - If Friday   → skip
            - If Saturday → skip
            - If in holiday list → skip
  Step 4: Keep moving forward until valid working day found
  Step 5: That date is the outflow booking date

  *** Outflow is NEVER booked on the auction date itself. ***

get_next_working_day(date, holidays) is the canonical implementation.
All other settlement helpers call it.
"""
import datetime
import logging
from typing import Optional, Set

log = logging.getLogger(__name__)

# Python weekday() values:  Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
_WORKING_WEEKDAYS = {0, 1, 2, 3, 6}   # Mon–Thu + Sun
_WEEKEND_DAYS     = {4, 5}             # Fri, Sat
_MAX_ITER         = 14                 # safety: no holiday block should exceed 14 days

# ── In-memory holiday set (loaded from DB or seed file at startup) ─────────────
_holiday_set: Set[datetime.date] = set()


def load_holidays(holidays: Set[datetime.date]) -> None:
    """
    Load the holiday set into memory.
    Call this at application start and whenever the holiday table changes.
    Weekends do NOT need to be included — they are handled by weekday check.
    """
    global _holiday_set
    _holiday_set = set(holidays)
    log.info("Holiday calendar loaded: %d public holidays", len(_holiday_set))


def get_loaded_holidays() -> frozenset:
    return frozenset(_holiday_set)


# ══════════════════════════════════════════════════════════════════════════════
# CORE FUNCTION — get_next_working_day
# This is the single source of truth for all working-day calculations.
# ══════════════════════════════════════════════════════════════════════════════

def get_next_working_day(
    date: datetime.date,
    holidays: Set[datetime.date] = None
) -> dict:
    """
    Find the first valid Bangladesh working day on or after `date`.

    Args:
        date:     The date to start searching from (inclusive).
        holidays: Optional explicit holiday set. If None, uses the
                  in-memory global set loaded by load_holidays().

    Returns a dict with full debug information:
        {
            "result_date":   datetime.date,   # the valid working day found
            "input_date":    datetime.date,   # the date we started from
            "skipped":       List[dict],      # each skipped day with reason
            "days_moved":    int,             # 0 if input_date itself is valid
            "adjusted":      bool,            # True if any days were skipped
        }

    Raises:
        ValueError if no working day found within _MAX_ITER days.
        This indicates a gap in the holiday calendar.
    """
    hols = holidays if holidays is not None else _holiday_set
    candidate = date
    skipped   = []

    for _ in range(_MAX_ITER):
        wd = candidate.weekday()

        # Check: is this a valid working day?
        if wd in _WORKING_WEEKDAYS and candidate not in hols:
            return {
                "result_date": candidate,
                "input_date":  date,
                "skipped":     skipped,
                "days_moved":  (candidate - date).days,
                "adjusted":    len(skipped) > 0,
            }

        # Not valid — record reason and move forward one day
        if wd == 4:
            reason = "FRIDAY"
        elif wd == 5:
            reason = "SATURDAY"
        elif candidate in hols:
            reason = "HOLIDAY"
        else:
            reason = "UNKNOWN"

        skipped.append({
            "date":   candidate,
            "reason": reason,
        })
        candidate += datetime.timedelta(days=1)

    raise ValueError(
        f"get_next_working_day: no valid working day found within {_MAX_ITER} "
        f"days of {date}. Check holiday calendar for missing entries."
    )


# ══════════════════════════════════════════════════════════════════════════════
# AUCTION SETTLEMENT — strict T+1 exclusive logic
# ══════════════════════════════════════════════════════════════════════════════

def auction_settlement_date(
    auction_date: datetime.date,
    holidays: Set[datetime.date] = None
) -> tuple:
    """
    Compute outflow booking date for an auction.

    Strict rule:
      Step 1: auction_date  (given)
      Step 2: start_date = auction_date + 1 calendar day   ← NEVER book on auction day
      Step 3-4: find first working day on or after start_date
      Step 5: that is the outflow date

    Returns:
        (settlement_date, roll_days, roll_reason_string, debug_dict)

    roll_days     = calendar days from start_date to settlement_date
                    (0 means start_date itself was already a working day)
    roll_reason   = human-readable string of every day skipped and why
    debug_dict    = full output from get_next_working_day for logging
    """
    hols       = holidays if holidays is not None else _holiday_set
    start_date = auction_date + datetime.timedelta(days=1)   # strict T+1 start
    result     = get_next_working_day(start_date, hols)

    settlement = result["result_date"]
    roll_days  = result["days_moved"]
    skipped    = result["skipped"]

    if skipped:
        parts = [f"{s['date']} ({s['reason']})" for s in skipped]
        roll_reason = "Skipped: " + ", ".join(parts)
    else:
        roll_reason = "No adjustment needed"

    return settlement, roll_days, roll_reason, result


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS for coupon/maturity date rolling
# ══════════════════════════════════════════════════════════════════════════════

def roll_date(raw_date: datetime.date, holidays: Set[datetime.date] = None) -> tuple:
    """
    Roll a coupon or maturity date forward to next working day (inclusive).
    Returns (payment_date, roll_days, roll_reason).
    """
    hols   = holidays if holidays is not None else _holiday_set
    result = get_next_working_day(raw_date, hols)

    payment   = result["result_date"]
    roll_days = result["days_moved"]
    skipped   = result["skipped"]

    if skipped:
        parts = [f"{s['date']} ({s['reason']})" for s in skipped]
        reason = "Skipped: " + ", ".join(parts)
    else:
        reason = ""

    return payment, roll_days, reason


def is_working_day(d: datetime.date, holidays: Set[datetime.date] = None) -> bool:
    """Return True if d is a valid Bangladesh working day."""
    hols = holidays if holidays is not None else _holiday_set
    return d.weekday() in _WORKING_WEEKDAYS and d not in hols


def is_weekend(d: datetime.date) -> bool:
    return d.weekday() in _WEEKEND_DAYS


def working_day_range(start: datetime.date, end: datetime.date,
                      holidays: Set[datetime.date] = None):
    """Yield every working day from start to end inclusive."""
    hols = holidays if holidays is not None else _holiday_set
    d = start
    while d <= end:
        if is_working_day(d, hols):
            yield d
        d += datetime.timedelta(days=1)


# ── Date parsing ───────────────────────────────────────────────────────────────

def parse_gsom_date(raw: str) -> Optional[datetime.date]:
    """
    Parse GSOM date strings:
      DD-MMM-YYYY  → bonds/FRTBs  e.g. '03-APR-2024'
      DD-MMM-YY   → T-Bills      e.g. '29-DEC-25'
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip().upper()
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    log.warning("Could not parse date string: '%s'", raw)
    return None
