"""
engine/aggregator.py

Aggregates maturity events, coupon events, and auction events
into daily_net_flow records.

One record per working day in the date range.
Zero-fills dates with no events (needed for chart continuity).

Net borrowing formula:
  net_borrowing = auction_outflow − (maturity_inflow + coupon_inflow)
  Positive → government is net new borrower.
  Negative → net repayment day.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)


def build_daily_flow(
    maturity_events: list[dict],
    coupon_events:   list[dict],
    auction_events:  list[dict],
    date_from:       date,
    date_to:         date,
    holiday_set:     Set[date],
) -> pd.DataFrame:
    """
    Build the daily_net_flow DataFrame for date_from..date_to.

    Returns one row per working day with columns:
      flow_date, coupon_inflow, principal_inflow, total_inflow,
      auction_outflow_planned, auction_outflow_confirmed, auction_outflow_best,
      net_borrowing, inflow_security_count, coupon_count, auction_tenor_count,
      data_complete
    """
    from config import BD_WORKING_WEEKDAYS

    # ── Index events by payment/settlement date ────────────────────────────────
    coupon_by_date:   dict[date, list] = defaultdict(list)
    maturity_by_date: dict[date, list] = defaultdict(list)
    auction_by_date:  dict[date, list] = defaultdict(list)

    for e in coupon_events:
        pd_ = e.get("payment_date")
        if pd_ and e.get("coupon_amount_bdt_mill") is not None:
            coupon_by_date[pd_].append(e)

    for e in maturity_events:
        pd_ = e.get("payment_date")
        if pd_ and e.get("principal_bdt_mill") is not None:
            maturity_by_date[pd_].append(e)

    for e in auction_events:
        sd = e.get("settlement_date")
        if sd and e.get("best_amount_bdt_mill") is not None:
            auction_by_date[sd].append(e)

    # ── Walk every working day in range ───────────────────────────────────────
    rows = []
    d = date_from
    while d <= date_to:
        # Skip non-working days
        if d.weekday() not in BD_WORKING_WEEKDAYS or d in holiday_set:
            d += timedelta(days=1)
            continue

        c_events = coupon_by_date.get(d, [])
        m_events = maturity_by_date.get(d, [])
        a_events = auction_by_date.get(d, [])

        coupon_inflow    = sum(e["coupon_amount_bdt_mill"] for e in c_events)
        principal_inflow = sum(e["principal_bdt_mill"] for e in m_events)
        total_inflow     = coupon_inflow + principal_inflow

        outflow_planned  = sum(
            e["offered_amount_bdt_mill"]
            for e in a_events
            if e.get("offered_amount_bdt_mill") is not None
        )
        outflow_confirmed = sum(
            e["accepted_amount_bdt_mill"]
            for e in a_events
            if e.get("accepted_amount_bdt_mill") is not None
        )
        outflow_best = sum(
            e["best_amount_bdt_mill"]
            for e in a_events
            if e.get("best_amount_bdt_mill") is not None
        )

        net_borrowing = outflow_best - total_inflow

        # Data completeness: all contributing events must be DQ_OK and CONFIRMED
        all_dq_ok = all(
            e.get("data_quality") == "OK"
            for e in (c_events + m_events + a_events)
        )
        no_planned = all(
            e.get("outflow_status") != "PLANNED"
            for e in a_events
        )
        data_complete = all_dq_ok and (no_planned or not a_events)

        # Breakdown by instrument type
        tbill_maturity = sum(
            e["principal_bdt_mill"]
            for e in m_events
            if e.get("security_type") == "T_BILL"
        )
        tbond_maturity = sum(
            e["principal_bdt_mill"]
            for e in m_events
            if e.get("security_type") == "T_BOND"
        )
        frtb_maturity = sum(
            e["principal_bdt_mill"]
            for e in m_events
            if e.get("security_type") == "FRTB"
        )
        tbill_outflow = sum(
            e["best_amount_bdt_mill"]
            for e in a_events
            if e.get("security_type") == "T_BILL"
               and e.get("best_amount_bdt_mill") is not None
        )
        bond_outflow = sum(
            e["best_amount_bdt_mill"]
            for e in a_events
            if e.get("security_type") in ("T_BOND", "FRTB")
               and e.get("best_amount_bdt_mill") is not None
        )

        rows.append({
            "flow_date":                  d,
            "coupon_inflow_bdt_mill":     round(coupon_inflow,    2),
            "principal_inflow_bdt_mill":  round(principal_inflow, 2),
            "total_inflow_bdt_mill":      round(total_inflow,     2),
            "auction_outflow_planned":    round(outflow_planned,  2),
            "auction_outflow_confirmed":  round(outflow_confirmed, 2),
            "auction_outflow_best":       round(outflow_best,     2),
            "net_borrowing_bdt_mill":     round(net_borrowing,    2),
            "tbill_maturity_bdt_mill":    round(tbill_maturity,   2),
            "tbond_maturity_bdt_mill":    round(tbond_maturity,   2),
            "frtb_maturity_bdt_mill":     round(frtb_maturity,    2),
            "tbill_outflow_bdt_mill":     round(tbill_outflow,    2),
            "bond_frtb_outflow_bdt_mill": round(bond_outflow,     2),
            "inflow_security_count":      len(m_events),
            "coupon_count":               len(c_events),
            "auction_tenor_count":        len(a_events),
            "data_complete":              data_complete,
        })

        d += timedelta(days=1)

    df = pd.DataFrame(rows)
    if df.empty:
        return _empty_df()

    # Cumulative net borrowing
    df["cumulative_net_borrowing"] = df["net_borrowing_bdt_mill"].cumsum().round(2)

    logger.info(
        "Daily flow built: %d working days from %s to %s",
        len(df), date_from, date_to,
    )
    return df


def _empty_df() -> pd.DataFrame:
    cols = [
        "flow_date", "coupon_inflow_bdt_mill", "principal_inflow_bdt_mill",
        "total_inflow_bdt_mill", "auction_outflow_planned", "auction_outflow_confirmed",
        "auction_outflow_best", "net_borrowing_bdt_mill", "tbill_maturity_bdt_mill",
        "tbond_maturity_bdt_mill", "frtb_maturity_bdt_mill", "tbill_outflow_bdt_mill",
        "bond_frtb_outflow_bdt_mill", "inflow_security_count", "coupon_count",
        "auction_tenor_count", "data_complete", "cumulative_net_borrowing",
    ]
    return pd.DataFrame(columns=cols)


def events_to_dataframe(events: list[dict]) -> pd.DataFrame:
    """Convert any list of event dicts to a DataFrame for display/export."""
    if not events:
        return pd.DataFrame()
    return pd.DataFrame(events)
