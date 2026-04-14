"""
dashboard/date_utils.py — Safe date helpers for Streamlit widgets.

Streamlit raises a hard exception if the default value passed to
st.date_input() is outside [min_value, max_value].
These helpers prevent that from ever happening.
"""
import datetime
from typing import Optional, Tuple, Sequence


def safe_date_input_value(
    default: datetime.date,
    min_date: datetime.date,
    max_date: datetime.date,
) -> datetime.date:
    """
    Clamp `default` into [min_date, max_date].

    Rules:
      - If default < min_date  → return min_date
      - If default > max_date  → return max_date
      - Otherwise              → return default unchanged

    Args:
        default:  The desired default date.
        min_date: The minimum allowed date (inclusive).
        max_date: The maximum allowed date (inclusive).

    Returns:
        A date guaranteed to satisfy min_date <= result <= max_date.
    """
    if min_date > max_date:
        # Degenerate range — return min_date as the only safe option
        return min_date
    if default < min_date:
        return min_date
    if default > max_date:
        return max_date
    return default


def safe_date_range(
    dates: Sequence,
) -> Optional[Tuple[datetime.date, datetime.date]]:
    """
    Compute (min_date, max_date) from a sequence of dates.

    Returns:
        (min_date, max_date) tuple, or None if the sequence is empty
        or contains no valid dates.

    The caller must check for None before using the result.
    """
    valid = []
    for d in dates:
        if d is None:
            continue
        if hasattr(d, "date"):          # pandas Timestamp → python date
            d = d.date()
        if isinstance(d, datetime.date):
            valid.append(d)

    if not valid:
        return None
    return min(valid), max(valid)


def clamp_date_to_data(
    desired: datetime.date,
    dates: Sequence,
    fallback: datetime.date = None,
) -> Optional[datetime.date]:
    """
    Clamp `desired` to the range spanned by `dates`.
    Returns `fallback` (or None) if `dates` is empty.
    """
    rng = safe_date_range(dates)
    if rng is None:
        return fallback
    return safe_date_input_value(desired, rng[0], rng[1])
