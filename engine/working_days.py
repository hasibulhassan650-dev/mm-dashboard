"""
engine/working_days.py

Bangladesh working-day rules:
  - Working days: Sunday, Monday, Tuesday, Wednesday, Thursday
  - Non-working:  Friday, Saturday
  - Bangladesh Bank holidays loaded from YAML seed files

Core function: next_working_day(d, inclusive, holiday_set)
  inclusive=True  → d itself counts (if it is a working day, return d)
  inclusive=False → start from d+1 (auction settlement: next day after auction)
"""

import logging
from datetime import date, timedelta
from typing import Optional, Set, FrozenSet
from pathlib import Path

import yaml

from config import (
    BD_WORKING_WEEKDAYS,
    HOLIDAY_DIR,
    MAX_HOLIDAY_ROLL_DAYS,
    DQ_HOLIDAY_UNCONFIRMED,
)

logger = logging.getLogger(__name__)

# ── Holiday set loading ────────────────────────────────────────────────────────

def load_holidays(fiscal_years: Optional[list] = None) -> Set[date]:
    """
    Load holiday dates from YAML seed files in HOLIDAY_DIR.

    fiscal_years: list of strings like ["2025-26", "2026-27"].
    If None, load all YAML files found.

    Returns a set of date objects. Moon-sighting-pending entries are
    included with a warning — they must be confirmed before use.
    """
    holidays: Set[date] = set()
    yaml_files = sorted(HOLIDAY_DIR.glob("*.yaml"))

    if not yaml_files:
        logger.error(
            "No holiday YAML files found in %s. "
            "Working-day calculations will be INCORRECT.",
            HOLIDAY_DIR,
        )
        return holidays

    for yaml_path in yaml_files:
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            logger.error("Failed to load holiday file %s: %s", yaml_path, e)
            continue

        fy = data.get("fiscal_year", "unknown")
        if fiscal_years and fy not in fiscal_years:
            continue

        for entry in data.get("holidays", []):
            raw_date = entry.get("date")
            h_type   = entry.get("type", "UNKNOWN")
            h_name   = entry.get("name", "")

            if not raw_date:
                continue

            try:
                d = date.fromisoformat(raw_date)
            except ValueError:
                logger.warning("Bad date in holiday file: %r", raw_date)
                continue

            if h_type == "MOON_SIGHTING_PENDING":
                logger.warning(
                    "Holiday %s (%s) is MOON_SIGHTING_PENDING — "
                    "confirm before relying on this date.",
                    raw_date, h_name,
                )

            holidays.add(d)

    logger.info("Loaded %d holidays from %d file(s)", len(holidays), len(yaml_files))
    return holidays


# ── Core algorithm ─────────────────────────────────────────────────────────────

def is_working_day(d: date, holiday_set: Set[date]) -> bool:
    """
    Return True if d is a Bangladesh working day:
      - weekday is Sun/Mon/Tue/Wed/Thu (not Fri or Sat)
      - d is not in holiday_set
    """
    return d.weekday() in BD_WORKING_WEEKDAYS and d not in holiday_set


def next_working_day(
    d: date,
    holiday_set: Set[date],
    inclusive: bool = True,
) -> date:
    """
    Return the next (or same, if inclusive) Bangladesh working day.

    inclusive=True:  if d is already a working day, return d.
    inclusive=False: always start from d + 1 (for auction settlement).

    Raises DataError if no working day is found within MAX_HOLIDAY_ROLL_DAYS.
    This protects against an incomplete holiday_set causing infinite loops.
    """
    candidate = d if inclusive else d + timedelta(days=1)
    skipped = []

    for _ in range(MAX_HOLIDAY_ROLL_DAYS + 1):
        if is_working_day(candidate, holiday_set):
            if skipped:
                reasons = ", ".join(skipped)
                logger.debug(
                    "next_working_day rolled %d day(s) from %s: skipped [%s] → %s",
                    len(skipped), d, reasons, candidate,
                )
            return candidate

        # Record skip reason for traceability
        if candidate.weekday() == 4:
            skipped.append(f"{candidate.isoformat()}(Fri)")
        elif candidate.weekday() == 5:
            skipped.append(f"{candidate.isoformat()}(Sat)")
        elif candidate in holiday_set:
            skipped.append(f"{candidate.isoformat()}(Holiday)")
        else:
            skipped.append(f"{candidate.isoformat()}(Unknown)")

        candidate += timedelta(days=1)

    raise DataError(
        f"No working day found within {MAX_HOLIDAY_ROLL_DAYS} days of {d}. "
        f"Check holiday_set coverage for this period."
    )


def roll_info(raw_date: date, holiday_set: Set[date]) -> dict:
    """
    Convenience: returns a dict with:
      scheduled_date, payment_date, days_rolled, roll_reason, was_rolled
    Used for traceability fields in maturity_events and coupon_events.
    """
    payment = next_working_day(raw_date, holiday_set, inclusive=True)
    skipped = []
    candidate = raw_date
    while candidate < payment:
        if candidate.weekday() == 4:
            skipped.append(f"{candidate.isoformat()}(Fri)")
        elif candidate.weekday() == 5:
            skipped.append(f"{candidate.isoformat()}(Sat)")
        elif candidate in holiday_set:
            skipped.append(f"{candidate.isoformat()}(Holiday)")
        candidate += timedelta(days=1)

    return {
        "scheduled_date": raw_date,
        "payment_date":   payment,
        "days_rolled":    (payment - raw_date).days,
        "roll_reason":    "; ".join(skipped) if skipped else "",
        "was_rolled":     payment != raw_date,
    }


def auction_settlement_date(auction_date: date, holiday_set: Set[date]) -> dict:
    """
    Auction settlement = next working day AFTER auction_date (inclusive=False).
    Returns roll_info dict with settlement_date in payment_date field.
    """
    return roll_info_from_exclusive(auction_date, holiday_set)


def roll_info_from_exclusive(raw_date: date, holiday_set: Set[date]) -> dict:
    """
    Like roll_info but starts from raw_date + 1 (exclusive).
    Used for auction settlement.
    """
    start = raw_date + timedelta(days=1)
    payment = next_working_day(start, holiday_set, inclusive=True)
    skipped = []
    candidate = raw_date + timedelta(days=1)
    while candidate < payment:
        if candidate.weekday() == 4:
            skipped.append(f"{candidate.isoformat()}(Fri)")
        elif candidate.weekday() == 5:
            skipped.append(f"{candidate.isoformat()}(Sat)")
        elif candidate in holiday_set:
            skipped.append(f"{candidate.isoformat()}(Holiday)")
        candidate += timedelta(days=1)

    return {
        "scheduled_date": raw_date,
        "payment_date":   payment,
        "days_rolled":    (payment - raw_date).days,
        "roll_reason":    "; ".join(skipped) if skipped else "",
        "was_rolled":     True,  # always True for exclusive roll
    }


class DataError(Exception):
    """Raised when a data integrity rule is violated."""
    pass
