"""
engines/aggregation.py — Build daily_net_flow from coupon, maturity, auction events.

All values in BDT million.
Net borrowing = auction outflow - (coupon inflow + principal inflow).
Positive net borrowing = government is net new borrower on that day.
"""
import datetime
import logging
from collections import defaultdict
from typing import List, Dict
import pandas as pd

from engines.outflow import best_outflow

log = logging.getLogger(__name__)


def build_daily_flows(
    coupon_events:   List[Dict],
    maturity_events: List[Dict],
    auction_events:  List[Dict],
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
) -> List[Dict]:
    """
    Aggregate all events into one record per working day.

    Returns list of dicts matching the daily_net_flow schema.
    Only working days with at least one event appear unless a full
    date range is passed (then zero rows are generated for empty days).
    """
    # Collect all unique event dates. Coupons/maturities are shown on their
    # SCHEDULED date (the real due date) — NOT dragged to the next working day
    # when the due date is a holiday/weekend. Auctions use their settlement date.
    dates = set()
    for e in coupon_events:
        dates.add(e["scheduled_date"])
    for e in maturity_events:
        dates.add(e["scheduled_date"])
    for e in auction_events:
        dates.add(e["settlement_date"])

    if date_from and date_to:
        from calendar_utils import working_day_range
        for d in working_day_range(date_from, date_to):
            dates.add(d)

    # Apply range filter
    if date_from:
        dates = {d for d in dates if d >= date_from}
    if date_to:
        dates = {d for d in dates if d <= date_to}

    now = datetime.datetime.utcnow()
    rows = []

    for flow_date in sorted(dates):
        # ── Inflow ────────────────────────────────────────────────────────────
        coupons_today = [e for e in coupon_events  if e["scheduled_date"] == flow_date]
        mats_today    = [e for e in maturity_events if e["scheduled_date"] == flow_date]

        coupon_inflow    = sum(e["amount_bdt_mill"]     for e in coupons_today)
        principal_inflow = sum(e["principal_bdt_mill"]  for e in mats_today)
        total_inflow     = coupon_inflow + principal_inflow

        # ── Outflow ───────────────────────────────────────────────────────────
        auctions_today = [e for e in auction_events if e["settlement_date"] == flow_date]

        planned_outflow   = sum(e.get("offered_amount_bdt_mill", 0.0)  for e in auctions_today)
        confirmed_outflow = sum(
            e["accepted_amount_bdt_mill"]
            for e in auctions_today
            if e.get("outflow_status") == "CONFIRMED"
            and e.get("accepted_amount_bdt_mill") is not None
        )
        best_out = sum(best_outflow(e) for e in auctions_today)

        net_borrowing = best_out - total_inflow

        # ── Data completeness ─────────────────────────────────────────────────
        all_events = coupons_today + mats_today + auctions_today
        data_complete = all(
            e.get("data_quality", "").startswith("OK") or e.get("data_quality") == "OK"
            for e in all_events
        ) and len(auctions_today) == 0 or all(
            e.get("outflow_status") == "CONFIRMED"
            for e in auctions_today
        )

        # Simpler: complete if no MISSING flags and outflow confirmed
        missing_flags = any(
            "MISSING" in (e.get("data_quality") or "")
            for e in all_events
        )
        unconfirmed = any(
            e.get("outflow_status") == "PLANNED"
            for e in auctions_today
        )
        data_complete = (not missing_flags) and (not unconfirmed)

        rows.append({
            "flow_date":                      flow_date,
            "coupon_inflow_bdt_mill":         round(coupon_inflow, 4),
            "principal_inflow_bdt_mill":      round(principal_inflow, 4),
            "total_inflow_bdt_mill":          round(total_inflow, 4),
            "auction_outflow_planned_mill":   round(planned_outflow, 4),
            "auction_outflow_confirmed_mill": round(confirmed_outflow, 4),
            "auction_outflow_best_mill":      round(best_out, 4),
            "net_borrowing_bdt_mill":         round(net_borrowing, 4),
            "inflow_security_count":          len({e["isin"] for e in mats_today}),
            "coupon_payment_count":           len(coupons_today),
            "data_complete":                  data_complete,
            "computed_utc":                   now,
        })

    log.info("Built %d daily flow rows", len(rows))
    return rows


def to_dataframe(rows: List[Dict]) -> pd.DataFrame:
    """Convert daily flow rows to DataFrame with proper dtypes."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["flow_date"] = pd.to_datetime(df["flow_date"])
    df["cumulative_net_borrowing"] = df["net_borrowing_bdt_mill"].cumsum()
    return df
