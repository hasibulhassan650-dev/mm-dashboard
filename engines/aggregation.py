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


def buyback_amount(event: Dict) -> float:
    """The cash a buyback put into the market, in BDT million.

    Same precedence as an auction's outflow, for the same reason: use the cash
    when it is published, the face value when it is not, and say which. BB's
    first buyback release (08-Oct-2026) gave 1,716.14 crore accepted and no
    price. The bond was quoted at a 7.96% yield against a 12.3% coupon, so it
    traded at a premium and the true cash was ABOVE par — showing par is wrong
    by that premium, and showing nothing is wrong by the whole 1,716 crore.
    Never derive a price: an invented number is the one error this project
    will not make.
    """
    from config import CRORE_TO_MILLION
    cash = event.get("cash_paid_crore")
    face = event.get("accepted_face_crore")
    crore = cash if cash is not None else (face or 0.0)
    return round(float(crore) * CRORE_TO_MILLION, 4)


def _is_half_yearly(freq) -> bool:
    f = (freq or "").lower().replace("-", " ").replace("_", " ")
    return "half" in f and "year" in f or "semi" in f


def sukuk_cash_flows(issues: List[Dict], date_from: datetime.date,
                     date_to: datetime.date) -> List[Dict]:
    """Government cash INTO the market from BGIS sukuk, in BDT million.

    A sukuk redeeming is the same cash event as a bond redeeming, and BB's
    outstanding table gives everything needed: amount, maturity, profit rate and
    how often profit is paid. Two rules keep this honest:

      * principal is always derivable - the outstanding amount, on the maturity
        date, rolled to the next working day like every other settlement;
      * profit is derived ONLY where BB states the frequency as half-yearly
        (amount x rate / 2 on each 6-month anniversary of the issue date, the last
        one coinciding with maturity). For "on Maturity" issues no profit is
        computed at all - the lump sum depends on the day count and the
        contract, and an invented figure is the one error this project will not
        make. Those issues contribute principal only, and say so (`kind`).
    """
    from dateutil.relativedelta import relativedelta

    from calendar_utils import roll_date
    from config import CRORE_TO_MILLION
    out: List[Dict] = []

    def add(raw: datetime.date, mill: float, kind: str, isin: str):
        pay = roll_date(raw)[0]
        if date_from <= pay <= date_to and mill:
            out.append({"payment_date": pay, "amount_bdt_mill": round(mill, 4),
                        "kind": kind, "isin": isin})

    for s in issues:
        crore = float(s.get("outstanding_crore") or 0.0)
        mat, iss = s.get("maturity_date"), s.get("issue_date")
        if not crore or not mat or not iss:
            continue
        add(mat, crore * CRORE_TO_MILLION, "PRINCIPAL", s["isin"])
        rate = s.get("profit_rate_pct")
        if rate is not None and _is_half_yearly(s.get("profit_frequency")):
            k = 1
            while True:
                d = iss + relativedelta(months=6 * k)
                if d > mat:
                    break
                add(d, crore * float(rate) / 100.0 / 2.0 * CRORE_TO_MILLION, "PROFIT", s["isin"])
                k += 1
    return out


def build_daily_flows(
    coupon_events:   List[Dict],
    maturity_events: List[Dict],
    auction_events:  List[Dict],
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
    buyback_events:  List[Dict] = None,
    sukuk_flows:     List[Dict] = None,
) -> List[Dict]:
    """
    Aggregate all events into one record per working day.

    Returns list of dicts matching the daily_net_flow schema.
    Only working days with at least one event appear unless a full
    date range is passed (then zero rows are generated for empty days).
    """
    buyback_events = buyback_events or []
    sukuk_flows = sukuk_flows or []
    # Collect all unique event dates. Coupons/maturities are bucketed on their
    # PAYMENT date — the working day the cash actually reaches the holder — not
    # the scheduled due date.
    #
    # This reverses an earlier deliberate choice to use scheduled_date, and the
    # reason is that this table feeds a LIQUIDITY ladder, not an accrual view.
    # When a coupon falls on a Friday or Saturday (the Bangladesh weekend) or a
    # holiday, BB settles it on the next working day; a desk planning funding
    # off the due date would be short on the day it actually needs the cash.
    # roll_date(), payment_date, roll_days and roll_reason all exist precisely
    # to model that settlement, and bucketing by scheduled_date discarded it.
    #
    # Measured impact when this was found: 31.9% of coupon events (BDT 2.0tn of
    # coupon value) and 5.1% of maturities were shown 1-3 days early. On
    # 2026-07-26 the ladder showed BDT 72mn against BDT 3,632mn actually paid.
    # The scheduled date is still stored on every event and shown in the
    # drill-down, so the accrual date is never lost.
    dates = set()
    for e in coupon_events:
        dates.add(e["payment_date"])
    for e in maturity_events:
        dates.add(e["payment_date"])
    for e in auction_events:
        dates.add(e["settlement_date"])

    if date_from and date_to:
        from calendar_utils import working_day_range
        for d in working_day_range(date_from, date_to):
            dates.add(d)

    for e in buyback_events:
        if e.get("settlement_date"):
            dates.add(e["settlement_date"])
    for e in sukuk_flows:
        dates.add(e["payment_date"])

    # Apply range filter
    if date_from:
        dates = {d for d in dates if d >= date_from}
    if date_to:
        dates = {d for d in dates if d <= date_to}

    now = datetime.datetime.utcnow()
    rows = []

    for flow_date in sorted(dates):
        # ── Inflow ────────────────────────────────────────────────────────────
        coupons_today = [e for e in coupon_events  if e["payment_date"] == flow_date]
        mats_today    = [e for e in maturity_events if e["payment_date"] == flow_date]

        # A buyback is an EARLY principal repayment: cash BB pays into the
        # market on a day nothing else explains. Its own line, because
        # principal_inflow reconciles against maturity_events and a buyback is
        # not a maturity — the bond goes on maturing, for less.
        buybacks_today = [e for e in buyback_events
                          if e.get("settlement_date") == flow_date]
        coupon_inflow    = sum(e["amount_bdt_mill"]     for e in coupons_today)
        principal_inflow = sum(e["principal_bdt_mill"]  for e in mats_today)
        buyback_inflow   = sum(buyback_amount(e)        for e in buybacks_today)
        # Government sukuk: principal and (half-yearly) profit, derived from BB's
        # outstanding table. Its own line for the same reason as the buyback.
        sukuk_inflow     = sum(e["amount_bdt_mill"] for e in sukuk_flows
                               if e["payment_date"] == flow_date)
        total_inflow     = coupon_inflow + principal_inflow + buyback_inflow + sukuk_inflow

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
            "buyback_inflow_bdt_mill":        round(buyback_inflow, 4),
            "sukuk_inflow_bdt_mill":          round(sukuk_inflow, 4),
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
