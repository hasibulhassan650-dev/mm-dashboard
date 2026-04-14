"""
engine/maturity.py

Generates one principal maturity event per ISIN.
All instrument types: T-Bill, T-Bond, FRTB.
"""

import logging
from datetime import date
from typing import Set

from config import CALC_PRINCIPAL, DQ_OK
from engine.working_days import roll_info

logger = logging.getLogger(__name__)


def generate_maturity_event(
    isin:                 str,
    maturity_date:        date,
    outstanding_bdt_mill: float,
    settlement_date:      date,
    source_page:          str,
    source_row_index:     int,
    holiday_set:          Set[date],
    security_type:        str,
    security_name:        str,
) -> dict:
    """
    Generate a single PRINCIPAL_MATURITY event dict for one ISIN.

    outstanding_bdt_mill: from GSOM — this is face value, not market value.
    """
    roll = roll_info(maturity_date, holiday_set)

    dq = DQ_OK
    if outstanding_bdt_mill is None:
        dq = "MISSING_OUTSTANDING"

    return {
        "isin":                       isin,
        "security_name":              security_name,
        "security_type":              security_type,
        "scheduled_date":             maturity_date,
        "payment_date":               roll["payment_date"],
        "days_rolled":                roll["days_rolled"],
        "roll_reason":                roll["roll_reason"],
        "was_rolled":                 roll["was_rolled"],
        "principal_bdt_mill":         outstanding_bdt_mill,
        "outstanding_used_bdt_mill":  outstanding_bdt_mill,
        "outstanding_snapshot_date":  settlement_date,
        "calc_method":                CALC_PRINCIPAL,
        "formula_string":             (
            f"{outstanding_bdt_mill:,.2f} [face value repayment, {security_type}]"
            if outstanding_bdt_mill is not None else "MISSING"
        ),
        "event_type":                 "PRINCIPAL_MATURITY",
        "source_page":                source_page,
        "source_row_index":           source_row_index,
        "data_quality":               dq,
    }


def generate_all_maturities(
    securities:  list[dict],
    holiday_set: Set[date],
) -> list[dict]:
    """Generate maturity events for all securities. One event per ISIN."""
    events = []
    seen_isins = set()

    for sec in securities:
        isin = sec["isin"]
        if isin in seen_isins:
            logger.warning("Duplicate ISIN %s in securities list — skipping second", isin)
            continue
        seen_isins.add(isin)

        if sec.get("maturity_date") is None:
            logger.warning("ISIN %s: missing maturity_date — skipping maturity event", isin)
            continue

        evt = generate_maturity_event(
            isin                 = isin,
            maturity_date        = sec["maturity_date"],
            outstanding_bdt_mill = sec.get("outstanding_bdt_mill"),
            settlement_date      = sec["settlement_date"],
            source_page          = sec["source_page"],
            source_row_index     = sec.get("source_row_index", 0),
            holiday_set          = holiday_set,
            security_type        = sec.get("security_type", "UNKNOWN"),
            security_name        = sec.get("security_name_normalized", ""),
        )
        events.append(evt)

    logger.info("Generated %d maturity events", len(events))
    return events
