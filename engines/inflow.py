"""
engines/inflow.py — Maturity and coupon event generation.

Rules:
  - One PRINCIPAL_MATURITY event per ISIN on the rolled maturity date.
  - COUPON events derived from next_coupon_date anchored schedule.
  - All amounts in BDT million.
  - Formula traceability stored in every event record.
"""
import datetime
import logging
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Optional

from calendar_utils import roll_date
from config import HFLY_DIVISOR, QRTY_DIVISOR, DAYS_IN_YEAR, OUTSTANDING_MIN

log = logging.getLogger(__name__)


# ── Maturity events ───────────────────────────────────────────────────────────

def generate_maturity_event(security: Dict) -> Optional[Dict]:
    """
    Generate one PRINCIPAL_MATURITY event for a security.
    Returns None if essential fields are missing.
    """
    isin       = security["isin"]
    mat_date   = security.get("maturity_date")
    outstanding = security.get("outstanding_bdt_mill")
    snap_date  = security.get("settlement_date")

    if mat_date is None:
        log.warning("ISIN %s: maturity_date is None — skipping maturity event", isin)
        return None
    if outstanding is None or outstanding < OUTSTANDING_MIN:
        log.warning("ISIN %s: outstanding is %s — skipping maturity event", isin, outstanding)
        return None

    payment_date, roll_days, roll_reason = roll_date(mat_date)

    return {
        "isin":                      isin,
        "scheduled_date":            mat_date,
        "payment_date":              payment_date,
        "principal_bdt_mill":        outstanding,
        "outstanding_snapshot_date": snap_date,
        "roll_days":                 roll_days,
        "roll_reason":               roll_reason,
        "calc_method":               "PRINCIPAL",
        "formula_string":            f"{outstanding:,.2f} [face value repayment on maturity]",
        "data_quality":              security.get("data_quality", "OK"),
    }


# ── Coupon schedule derivation ────────────────────────────────────────────────

def _period_months(freq: str) -> int:
    return 6 if freq == "HFLY" else 3

def _divisor(freq: str) -> int:
    return HFLY_DIVISOR if freq == "HFLY" else QRTY_DIVISOR

def _coupon_amount_approx(outstanding: float, rate_pct: float, freq: str) -> float:
    """Simplified coupon: rate / divisor. Used for dashboard display."""
    return outstanding * (rate_pct / 100) / _divisor(freq)

def _coupon_amount_act365(outstanding: float, rate_pct: float, days: int) -> float:
    """Exact coupon using actual days / 365 convention."""
    return outstanding * (rate_pct / 100) * (days / DAYS_IN_YEAR)

def _calc_method(freq: str, use_act365: bool) -> str:
    base = "ACT365" if use_act365 else "APPROX"
    return f"{base}_{freq}"


def generate_coupon_schedule(security: Dict) -> List[Dict]:
    """
    Generate all coupon events for a bond or FRTB.
    Returns empty list for T-Bills or securities with missing coupon data.

    Algorithm:
    1. Anchor on next_coupon_date from GSOM snapshot.
    2. Step backward to issue_date to build historical schedule.
    3. Step forward to maturity_date to build future schedule.
    4. Roll each scheduled_date to next working day.
    5. Calculate coupon amount (APPROX method; ACT365 where days are known).
    """
    isin        = security["isin"]
    freq        = security.get("coupon_frequency", "NONE")
    security_type = security.get("security_type")

    # T-Bills and securities with NONE frequency → no coupons
    if security_type == "T_BILL" or freq == "NONE":
        return []

    next_cd     = security.get("next_coupon_date")
    last_cd     = security.get("last_coupon_date")
    issue_date  = security.get("issue_date")
    mat_date    = security.get("maturity_date")
    rate_pct    = security.get("coupon_rate_pct")
    outstanding = security.get("outstanding_bdt_mill")
    snap_date   = security.get("settlement_date")

    # Validate essentials. We anchor on the bond's true ISSUE + MATURITY terms
    # (never the rolled GSOM next_coupon_date), so next_coupon_date is no longer
    # required — it can be missing or already pushed to a working day.
    missing = [k for k, v in [
        ("issue_date", issue_date),    ("maturity_date", mat_date),
        ("coupon_rate_pct", rate_pct), ("outstanding_bdt_mill", outstanding)
    ] if v is None]
    if missing:
        log.warning("ISIN %s: missing %s — cannot generate coupon schedule", isin, missing)
        return []

    period_m = _period_months(freq)

    # ── Canonical coupon dates ────────────────────────────────────────────────
    # Coupons fall on the ISSUE-date anniversary, every `period_m` months, from
    # the first coupon after issue up to and including maturity. Each date is
    # computed as issue + n×period from the FIXED issue anchor (never
    # cumulatively) so the day-of-month can never drift, and never inherits a
    # rolled next_coupon_date. BB publishes next_coupon_date ALREADY pushed to
    # the next working day; anchoring on it would corrupt the schedule's
    # day-of-month for the bond's entire life. e.g. BD0928461054 issued
    # 14-Jun-2023 → every coupon lands on the 14th; 14-Jun-2026 is a Sunday
    # (a Bangladesh working day, weekend is Fri–Sat) so it is genuinely the
    # 14th and is NOT dragged.
    sched_dates = []
    n = 1
    while True:
        d = issue_date + relativedelta(months=period_m * n)
        if d > mat_date:
            break
        sched_dates.append(d)
        n += 1
    # The final coupon is paid alongside principal on the maturity date —
    # guarantee it is present even if issue+n×period lands a day off maturity.
    if not sched_dates or sched_dates[-1] != mat_date:
        sched_dates = [d for d in sched_dates if d < mat_date]
        sched_dates.append(mat_date)

    events = [(d, False) for d in sched_dates]

    # ── Detect short first coupon ─────────────────────────────────────────────
    first_date = events[0][0] if events else None
    short_first = False
    if first_date and issue_date:
        expected_days = period_m * 30   # rough lower bound
        actual_first_days = (first_date - issue_date).days
        short_first = actual_first_days < expected_days

    # ── Generate event records ────────────────────────────────────────────────
    result = []
    for i, (sched, is_derived) in enumerate(events):
        # Coupon is "from source" if it lines up (within a few days, since GSOM
        # dates are rolled) with the reported next/last coupon date.
        from_source = (
            (next_cd is not None and abs((sched - next_cd).days) <= 4) or
            (last_cd is not None and abs((sched - last_cd).days) <= 4)
        )
        is_derived_flag = not from_source

        period_days = (sched - events[i-1][0]).days if i > 0 else None

        if i == 0 and short_first:
            # Genuine short FIRST coupon (bond issued mid-period) is truly
            # partial — pro-rate by actual days since issue.
            period_days = (sched - issue_date).days
            amount      = _coupon_amount_act365(outstanding, rate_pct, period_days)
            method      = _calc_method(freq, use_act365=True)
            formula     = (
                f"{outstanding:,.2f} × ({rate_pct}/100) × ({period_days}/365)"
                f" = {amount:,.4f} [ACT365_SHORT_FIRST]"
            )
        else:
            # Every standard periodic coupon = face × rate ÷ payments-per-year
            # (2 for half-yearly, 4 for quarterly). Market convention; not
            # day-count-adjusted. e.g. BD0928221052 → face×rate/2 = 4621.38.
            amount  = _coupon_amount_approx(outstanding, rate_pct, freq)
            method  = _calc_method(freq, use_act365=False)
            formula = (
                f"{outstanding:,.2f} × ({rate_pct}/100) / {_divisor(freq)}"
                f" = {amount:,.4f} [{method}]"
            )

        payment_date, roll_days, roll_reason = roll_date(sched)

        dq = "OK"
        if sched > mat_date:
            dq = "COUPON_PAST_MATURITY"
        elif amount <= 0:
            dq = "ZERO_COUPON"

        result.append({
            "isin":                      isin,
            "scheduled_date":            sched,
            "payment_date":              payment_date,
            "amount_bdt_mill":           round(amount, 4),
            "coupon_rate_used_pct":      rate_pct,
            "outstanding_used_bdt_mill": outstanding,
            "outstanding_snapshot_date": snap_date,
            "period_days_actual":        period_days,
            "calc_method":               method,
            "formula_string":            formula,
            "is_derived":                is_derived_flag,
            "is_short_coupon":           (i == 0 and short_first),
            "data_quality":              dq,
        })

    log.info("ISIN %s: generated %d coupon events (%s)", isin, len(result), freq)
    return result
