"""
engines/outflow.py — Auction outflow event generation.

STRICT SETTLEMENT RULE:
  - Outflow is NEVER booked on the auction date.
  - Start from auction_date + 1 calendar day.
  - Move forward until first valid Bangladesh working day.
  - Both T-Bill and T-Bond auctions follow identical logic.
  - Amounts stored in both crore (raw source) and million (canonical).
  - Every event carries full debug trace: auction_date, adjusted_outflow_date,
    reason for any adjustment.
"""
import logging
import datetime
from typing import List, Dict, Set, Optional

from calendar_utils import auction_settlement_date, get_next_working_day
from config import CRORE_TO_MILLION, fiscal_year

log = logging.getLogger(__name__)


def generate_auction_event(
    row: Dict,
    holidays: Set[datetime.date] = None
) -> Dict:
    """
    Convert a raw auction row into a fully enriched auction event dict.

    Applies strict outflow date logic:
      1. Take auction_date from row
      2. Compute start = auction_date + 1
      3. Walk forward until valid working day
      4. That date = settlement_date (outflow booking date)

    Every event carries:
      - auction_date          (original auction date)
      - adjusted_outflow_date (final settlement date)
      - adjustment_reason     (explains any weekend/holiday skips)
      - roll_days             (calendar days moved from T+1)

    Args:
        row:      Dict from fetchers/auction.py with at minimum:
                    auction_date, tenor_label, security_type,
                    offered_amount_bdt_crore
        holidays: Optional explicit holiday set (uses global if None)

    Returns:
        Enriched event dict ready for DB insert and dashboard display.
    """
    adate = row["auction_date"]

    # ── Step 1-4: strict next-working-day after auction ───────────────────────
    settlement, roll_days, roll_reason, debug = auction_settlement_date(
        adate, holidays
    )

    # ── Amounts ───────────────────────────────────────────────────────────────
    offered_crore = float(row.get("offered_amount_bdt_crore") or 0.0)
    offered_mill  = round(offered_crore * CRORE_TO_MILLION, 4)

    accepted_crore = row.get("accepted_amount_bdt_crore")
    accepted_mill  = (
        round(float(accepted_crore) * CRORE_TO_MILLION, 4)
        if accepted_crore is not None else None
    )

    # ── Build event ───────────────────────────────────────────────────────────
    event = {
        # Identity
        "fiscal_year":  row.get("fiscal_year", fiscal_year(adate)),
        "auction_no":   row.get("auction_no"),
        "tenor_label":  row.get("tenor_label", "UNKNOWN"),
        "security_type": row.get("security_type", "UNKNOWN"),

        # Instrument (pass-through from raw row for dashboard display)
        "instrument_type":        row.get("instrument_type", "UNKNOWN"),
        "source_section":         row.get("source_section", ""),

        # Dates — the most important fields
        "auction_date":           adate,
        "adjusted_outflow_date":  settlement,   # canonical outflow date
        "settlement_date":        settlement,   # alias used by aggregation

        # Adjustment debug — mandatory per spec
        "adjustment_reason":      roll_reason,
        "roll_days":              roll_days,
        "skipped_dates":          [
            f"{s['date']} ({s['reason']})"
            for s in debug.get("skipped", [])
        ],

        # Amounts (both units stored for traceability)
        "offered_amount_bdt_crore":  offered_crore,
        "offered_amount_bdt_mill":   offered_mill,
        "accepted_amount_bdt_crore": accepted_crore,
        "accepted_amount_bdt_mill":  accepted_mill,
        "weighted_avg_yield_pct":    row.get("weighted_avg_yield_pct"),

        # Status
        "outflow_status":  row.get("outflow_status", "PLANNED"),
        "resulting_isin":  row.get("resulting_isin"),
        "source":          row.get("source", "SEED_CSV"),
        "data_quality":    "OK",
    }

    # ── Validation ────────────────────────────────────────────────────────────
    flags = []

    # Settlement must not be the auction date
    if settlement == adate:
        flags.append("OUTFLOW_ON_AUCTION_DATE")   # this must never happen

    # Settlement must not be a weekend
    wd = settlement.weekday()
    if wd in {4, 5}:
        flags.append(f"SETTLEMENT_ON_WEEKEND({settlement.strftime('%a')})")

    # Amount checks
    if offered_crore == 0:
        flags.append("ZERO_OFFERED")

    if accepted_mill is not None:
        if accepted_mill > offered_mill:
            flags.append("ACCEPTED_EXCEEDS_OFFERED")
        if accepted_mill < 0:
            flags.append("NEGATIVE_ACCEPTED")

    if roll_days > 7:
        flags.append(f"LONG_ROLL_{roll_days}D")

    if flags:
        event["data_quality"] = ", ".join(flags)

    # ── Debug log per spec requirement ────────────────────────────────────────
    log.info(
        "AUCTION | %s %-4s | auction_date=%s | adjusted_outflow_date=%s | "
        "roll=%dd | reason: %s",
        event["security_type"],
        event["tenor_label"],
        adate,
        settlement,
        roll_days,
        roll_reason,
    )

    return event


def generate_all_auction_events(
    raw_rows: List[Dict],
    holidays: Set[datetime.date] = None
) -> List[Dict]:
    """
    Process all auction rows (both T-Bill and T-Bond) uniformly.
    Returns list of enriched event dicts with one-per-tenor-per-auction-date.

    Validates post-processing:
      - Every auction has exactly one outflow date
      - No outflow falls on Friday, Saturday, or holiday
      - Prints summary table to log
    """
    events = []
    errors = []

    for row in raw_rows:
        try:
            evt = generate_auction_event(row, holidays)
            events.append(evt)
        except Exception as exc:
            msg = (f"Failed to process auction row "
                   f"{row.get('auction_date')} {row.get('tenor_label')}: {exc}")
            log.error(msg)
            errors.append(msg)

    # ── Post-generation validation ────────────────────────────────────────────
    hols = holidays or set()
    log.info("=" * 60)
    log.info("AUCTION OUTFLOW SUMMARY (%d events, %d errors)", len(events), len(errors))
    log.info("%-12s %-6s %-6s %-12s %-12s %s",
             "Type", "Tenor", "AucNo", "Auction Date", "Outflow Date", "Adjustment")

    for evt in events:
        sd = evt["adjusted_outflow_date"]

        # Hard check: no outflow on weekend or holiday
        if sd.weekday() in {4, 5}:
            evt["data_quality"] = (evt["data_quality"] or "") + " | FATAL_WEEKEND_SETTLEMENT"
            log.error("FATAL: outflow on weekend for %s %s on %s",
                      evt["security_type"], evt["tenor_label"], sd)
        if sd in hols:
            evt["data_quality"] = (evt["data_quality"] or "") + " | FATAL_HOLIDAY_SETTLEMENT"
            log.error("FATAL: outflow on holiday for %s %s on %s",
                      evt["security_type"], evt["tenor_label"], sd)

        log.info("%-12s %-6s %-6s %-12s %-12s %s",
                 evt["security_type"],
                 evt["tenor_label"],
                 str(evt["auction_no"] or ""),
                 str(evt["auction_date"]),
                 str(sd),
                 evt["adjustment_reason"])

    log.info("=" * 60)

    if errors:
        log.warning("%d auction rows failed processing", len(errors))

    return events


def best_outflow(event: Dict) -> float:
    """
    Best-available outflow amount.
    Uses accepted (confirmed) amount when available; falls back to offered.
    """
    if (event.get("outflow_status") == "CONFIRMED"
            and event.get("accepted_amount_bdt_mill") is not None):
        return float(event["accepted_amount_bdt_mill"])
    return float(event.get("offered_amount_bdt_mill", 0.0))
