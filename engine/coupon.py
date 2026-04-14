"""
engine/coupon.py

Generates all coupon events for T-Bond and FRTB securities.
T-Bills are explicitly excluded.

For each security, produces one dict per coupon payment date:
  - Uses next_coupon_date and last_coupon_date from GSOM as anchors.
  - Back-derives historical dates to issue_date.
  - Forward-derives future dates to maturity_date.
  - Applies working_day_roll to each scheduled date.
  - Detects short first coupon.
  - Tags calc_method, formula_string, is_derived on every row.

Coupon formula (APPROX — default for dashboard):
  amount = outstanding × (rate/100) / frequency_divisor

Exact formula (ACT365 — optional, if period days are available):
  amount = outstanding × (rate/100) × (actual_days / 365)
"""

import logging
from datetime import date
from dateutil.relativedelta import relativedelta
from typing import Optional, Set

from config import (
    INSTRUMENT_T_BILL, FREQ_NONE, FREQ_CONFIG,
    FREQ_HFLY, FREQ_QRTY,
    CALC_APPROX_HFLY, CALC_APPROX_QRTY,
    CALC_ACT365_HFLY, CALC_ACT365_QRTY,
    DQ_OK, DQ_MISSING_COUPON_DATE, DQ_COUPON_GAP_ANOMALY,
    DQ_SHORT_COUPON, DQ_MISSING_OUTSTANDING,
)
from engine.working_days import roll_info

logger = logging.getLogger(__name__)

# Tolerance for coupon gap check (days)
_HFLY_GAP_MIN, _HFLY_GAP_MAX = 175, 190
_QRTY_GAP_MIN, _QRTY_GAP_MAX = 85,  95


def _coupon_gap_ok(last: date, next_: date, freq: str) -> bool:
    gap = (next_ - last).days
    if freq == FREQ_HFLY:
        return _HFLY_GAP_MIN <= gap <= _HFLY_GAP_MAX
    elif freq == FREQ_QRTY:
        return _QRTY_GAP_MIN <= gap <= _QRTY_GAP_MAX
    return True


def _approx_coupon(outstanding: float, rate_pct: float, freq: str) -> float:
    divisor = FREQ_CONFIG[freq]["divisor"]
    return round(outstanding * (rate_pct / 100) / divisor, 2)


def _act365_coupon(outstanding: float, rate_pct: float, period_days: int) -> float:
    return round(outstanding * (rate_pct / 100) * (period_days / 365), 2)


def _calc_method(freq: str) -> str:
    return CALC_APPROX_HFLY if freq == FREQ_HFLY else CALC_APPROX_QRTY


def _formula_string(outstanding: float, rate_pct: float, freq: str, amount: float) -> str:
    divisor = FREQ_CONFIG[freq]["divisor"]
    return (
        f"{outstanding:,.2f} × ({rate_pct:.4f}/100) / {divisor} = {amount:,.2f} "
        f"[{_calc_method(freq)}]"
    )


def generate_coupon_schedule(
    isin:               str,
    issue_date:         date,
    maturity_date:      date,
    coupon_rate_pct:    float,
    coupon_frequency:   str,
    last_coupon_date:   Optional[date],
    next_coupon_date:   Optional[date],
    outstanding_bdt_mill: float,
    settlement_date:    date,         # snapshot date — for traceability
    source_page:        str,
    holiday_set:        Set[date],
    use_act365:         bool = False,
) -> list[dict]:
    """
    Generate all coupon events for one security.

    Returns list of event dicts, one per coupon date.
    Returns [] for T-Bills or if mandatory fields are missing.
    """
    # Guard: T-Bills have no coupons
    if coupon_frequency == FREQ_NONE:
        return []

    if coupon_frequency not in FREQ_CONFIG:
        logger.error("Unknown coupon frequency %r for ISIN %s", coupon_frequency, isin)
        return []

    if outstanding_bdt_mill is None:
        logger.warning("ISIN %s: missing outstanding — cannot generate coupons", isin)
        return []

    if coupon_rate_pct is None:
        logger.warning("ISIN %s: missing coupon rate — cannot generate coupons", isin)
        return []

    # We need at least one anchor date
    if next_coupon_date is None and last_coupon_date is None:
        logger.warning("ISIN %s: both last and next coupon dates missing", isin)
        return _empty_with_flag(isin, DQ_MISSING_COUPON_DATE)

    months_per_period = FREQ_CONFIG[coupon_frequency]["months"]

    # Anchor: prefer next_coupon_date; fall back to last + period
    if next_coupon_date is not None:
        anchor = next_coupon_date
    else:
        anchor = last_coupon_date + relativedelta(months=months_per_period)

    # Validate gap if both dates available
    dq_flag = DQ_OK
    if last_coupon_date and next_coupon_date:
        if not _coupon_gap_ok(last_coupon_date, next_coupon_date, coupon_frequency):
            gap = (next_coupon_date - last_coupon_date).days
            logger.warning(
                "ISIN %s: coupon gap %d days is anomalous for %s",
                isin, gap, coupon_frequency,
            )
            dq_flag = DQ_COUPON_GAP_ANOMALY

    # ── Build full schedule ────────────────────────────────────────────────────
    scheduled_dates = set()

    # Forward from anchor to maturity
    d = anchor
    while d <= maturity_date:
        scheduled_dates.add(d)
        d += relativedelta(months=months_per_period)

    # Backward from anchor to issue_date (derives historical dates)
    d = anchor - relativedelta(months=months_per_period)
    while d >= issue_date:
        scheduled_dates.add(d)
        d -= relativedelta(months=months_per_period)

    # Sort ascending
    sorted_dates = sorted(scheduled_dates)

    if not sorted_dates:
        logger.warning("ISIN %s: no coupon dates generated", isin)
        return []

    # ── First coupon: detect short period ─────────────────────────────────────
    first_date = sorted_dates[0]
    expected_full_period_days = months_per_period * 30  # approx floor
    is_short_first = (first_date - issue_date).days < expected_full_period_days

    # ── Generate event records ─────────────────────────────────────────────────
    events = []
    directly_from_gsom = {next_coupon_date, last_coupon_date} - {None}

    for idx, sched in enumerate(sorted_dates):
        is_first = (sched == first_date)
        is_derived = sched not in directly_from_gsom

        # Determine prev date for ACT365 period-days calculation
        prev_date = sorted_dates[idx - 1] if idx > 0 else issue_date

        if use_act365:
            period_days = (sched - prev_date).days
            amount = _act365_coupon(outstanding_bdt_mill, coupon_rate_pct, period_days)
            calc_method = CALC_ACT365_HFLY if coupon_frequency == FREQ_HFLY else CALC_ACT365_QRTY
            formula = (
                f"{outstanding_bdt_mill:,.2f} × ({coupon_rate_pct:.4f}/100) "
                f"× ({period_days}/365) = {amount:,.2f} [{calc_method}]"
            )
        else:
            period_days = None
            amount = _approx_coupon(outstanding_bdt_mill, coupon_rate_pct, coupon_frequency)
            calc_method = _calc_method(coupon_frequency)
            formula = _formula_string(outstanding_bdt_mill, coupon_rate_pct, coupon_frequency, amount)

        # Short first coupon override — always use ACT365 for short period
        if is_first and is_short_first:
            period_days_short = (sched - issue_date).days
            amount = _act365_coupon(outstanding_bdt_mill, coupon_rate_pct, period_days_short)
            calc_method = DQ_SHORT_COUPON
            formula = (
                f"{outstanding_bdt_mill:,.2f} × ({coupon_rate_pct:.4f}/100) "
                f"× ({period_days_short}/365) = {amount:,.2f} [SHORT_COUPON_ACT365]"
            )

        # Roll to working day
        roll = roll_info(sched, holiday_set)
        row_dq = dq_flag
        if roll["was_rolled"] and row_dq == DQ_OK:
            row_dq = "ROLLED"

        events.append({
            "isin":                       isin,
            "scheduled_date":             sched,
            "payment_date":               roll["payment_date"],
            "days_rolled":                roll["days_rolled"],
            "roll_reason":                roll["roll_reason"],
            "was_rolled":                 roll["was_rolled"],
            "coupon_amount_bdt_mill":     amount,
            "outstanding_used_bdt_mill":  outstanding_bdt_mill,
            "outstanding_snapshot_date":  settlement_date,
            "coupon_rate_used_pct":       coupon_rate_pct,
            "coupon_frequency":           coupon_frequency,
            "period_days_actual":         period_days,
            "calc_method":                calc_method,
            "formula_string":             formula,
            "is_derived":                 is_derived,
            "is_short_coupon":            is_first and is_short_first,
            "event_type":                 "COUPON",
            "source_page":                source_page,
            "data_quality":               row_dq,
        })

    logger.info("Generated %d coupon events for ISIN %s", len(events), isin)
    return events


def _empty_with_flag(isin: str, flag: str) -> list[dict]:
    return [{
        "isin": isin,
        "event_type": "COUPON",
        "data_quality": flag,
        "coupon_amount_bdt_mill": None,
    }]


def generate_all_coupons(
    securities: list[dict],
    holiday_set: Set[date],
    use_act365: bool = False,
) -> list[dict]:
    """
    Generate coupon events for all securities in the list.
    Skips T-Bills automatically.
    Returns flat list of all coupon event dicts.
    """
    all_events = []
    for sec in securities:
        if sec.get("security_type") == INSTRUMENT_T_BILL:
            continue
        if sec.get("coupon_frequency") == FREQ_NONE:
            continue

        events = generate_coupon_schedule(
            isin              = sec["isin"],
            issue_date        = sec["issue_date"],
            maturity_date     = sec["maturity_date"],
            coupon_rate_pct   = sec.get("coupon_rate_pct"),
            coupon_frequency  = sec.get("coupon_frequency"),
            last_coupon_date  = sec.get("last_coupon_date"),
            next_coupon_date  = sec.get("next_coupon_date"),
            outstanding_bdt_mill = sec.get("outstanding_bdt_mill"),
            settlement_date   = sec["settlement_date"],
            source_page       = sec["source_page"],
            holiday_set       = holiday_set,
            use_act365        = use_act365,
        )
        all_events.extend(events)

    return all_events
