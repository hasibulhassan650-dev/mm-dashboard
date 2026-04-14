"""
engine/auction_engine.py

Loads auction data from manual YAML seeds and computes settlement dates.

Settlement rule: next working day AFTER auction_date (exclusive roll).
Amount unit: YAML stores BDT crore → convert to BDT million (× 10).

Two-phase data model:
  Phase 1 (pre-auction):  outflow_status=PLANNED, accepted=None
  Phase 2 (post-auction): outflow_status=CONFIRMED, accepted filled from press release
"""

import logging
from datetime import date
from pathlib import Path
from typing import Set

import yaml

from config import (
    AUCTION_MANUAL_DIR, CRORE_TO_MILLION, DQ_OK,
)
from engine.working_days import roll_info_from_exclusive
from parser.dates import parse_iso

logger = logging.getLogger(__name__)


def _crore_to_mill(val) -> float:
    """Convert BDT crore to BDT million. None → None."""
    if val is None:
        return None
    return round(float(val) * CRORE_TO_MILLION, 2)


def load_tbill_auctions(holiday_set: Set[date]) -> list[dict]:
    """
    Load all T-Bill auction YAML files from AUCTION_MANUAL_DIR.
    Returns list of auction event dicts, one per (auction_date, tenor).
    """
    events = []
    yaml_files = sorted(AUCTION_MANUAL_DIR.glob("tbill_*.yaml"))

    if not yaml_files:
        logger.warning(
            "No T-Bill auction YAML files found in %s. "
            "Auction outflow will be missing.",
            AUCTION_MANUAL_DIR,
        )
        return []

    for yaml_path in yaml_files:
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            logger.error("Failed to load auction file %s: %s", yaml_path, e)
            continue

        fiscal_year = data.get("fiscal_year", "unknown")

        for auc in data.get("auctions", []):
            auction_no   = auc.get("auction_no")
            auction_date_s = auc.get("auction_date")

            if not auction_date_s:
                logger.warning("Auction entry missing auction_date in %s", yaml_path)
                continue

            try:
                auction_date = parse_iso(auction_date_s)
            except ValueError as e:
                logger.warning("Bad auction_date %r in %s: %s", auction_date_s, yaml_path, e)
                continue

            # Compute settlement: exclusive next working day
            try:
                roll = roll_info_from_exclusive(auction_date, holiday_set)
                settlement_date = roll["payment_date"]
                roll_days       = roll["days_rolled"]
                roll_reason     = roll["roll_reason"]
            except Exception as e:
                logger.error(
                    "Settlement date calculation failed for auction %s on %s: %s",
                    auction_no, auction_date, e,
                )
                settlement_date = None
                roll_days, roll_reason = None, str(e)

            for tenor_rec in auc.get("tenors", []):
                tenor        = tenor_rec.get("tenor")
                offered_c    = tenor_rec.get("offered_crore")
                accepted_c   = tenor_rec.get("accepted_crore")
                wt_yield     = tenor_rec.get("weighted_avg_yield_pct")
                status       = tenor_rec.get("outflow_status", "PLANNED")

                if offered_c is None:
                    logger.warning(
                        "Missing offered_crore for auction %s tenor %s — skipping",
                        auction_no, tenor,
                    )
                    continue

                offered_mill  = _crore_to_mill(offered_c)
                accepted_mill = _crore_to_mill(accepted_c)

                # Best outflow: confirmed if available, else planned
                best_mill = accepted_mill if accepted_mill is not None else offered_mill

                events.append({
                    "auction_no":               f"{auction_no}/{fiscal_year}",
                    "auction_no_int":           auction_no,
                    "fiscal_year":              fiscal_year,
                    "auction_date":             auction_date,
                    "settlement_date":          settlement_date,
                    "days_rolled":              roll_days,
                    "roll_reason":              roll_reason,
                    "security_type":            "T_BILL",
                    "tenor_label":              tenor,
                    "offered_amount_bdt_crore": offered_c,
                    "offered_amount_bdt_mill":  offered_mill,
                    "accepted_amount_bdt_crore": accepted_c,
                    "accepted_amount_bdt_mill":  accepted_mill,
                    "best_amount_bdt_mill":      best_mill,
                    "weighted_avg_yield_pct":   wt_yield,
                    "outflow_status":           status,
                    "event_type":               "AUCTION",
                    "source":                   str(yaml_path.name),
                    "data_quality":             DQ_OK if settlement_date else "MISSING_SETTLEMENT",
                })

    logger.info("Loaded %d auction tenor events from %d file(s)", len(events), len(yaml_files))
    return events


def load_bond_auctions(holiday_set: Set[date]) -> list[dict]:
    """
    Load T-Bond and FRTB auction YAML files.
    Same structure as T-Bill but security_type varies.
    """
    events = []
    yaml_files = sorted(AUCTION_MANUAL_DIR.glob("bond_*.yaml")) + \
                 sorted(AUCTION_MANUAL_DIR.glob("frtb_*.yaml"))

    for yaml_path in yaml_files:
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            logger.error("Failed to load %s: %s", yaml_path, e)
            continue

        fiscal_year = data.get("fiscal_year", "unknown")
        sec_type    = data.get("security_type", "T_BOND")

        for auc in data.get("auctions", []):
            auction_no    = auc.get("auction_no")
            auction_date_s = auc.get("auction_date")
            tenor         = auc.get("tenor")
            offered_c     = auc.get("offered_crore")
            accepted_c    = auc.get("accepted_crore")
            wt_yield      = auc.get("weighted_avg_yield_pct")
            status        = auc.get("outflow_status", "PLANNED")
            resulting_isin = auc.get("resulting_isin")

            if not auction_date_s or offered_c is None:
                continue

            try:
                auction_date = parse_iso(auction_date_s)
                roll = roll_info_from_exclusive(auction_date, holiday_set)
                settlement_date = roll["payment_date"]
            except Exception as e:
                logger.error("Bond auction %s: %s", auction_no, e)
                settlement_date = None

            offered_mill  = _crore_to_mill(offered_c)
            accepted_mill = _crore_to_mill(accepted_c)

            events.append({
                "auction_no":               f"{auction_no}/{fiscal_year}",
                "auction_no_int":           auction_no,
                "fiscal_year":              fiscal_year,
                "auction_date":             auction_date,
                "settlement_date":          settlement_date,
                "security_type":            sec_type,
                "tenor_label":              tenor,
                "offered_amount_bdt_crore": offered_c,
                "offered_amount_bdt_mill":  offered_mill,
                "accepted_amount_bdt_crore": accepted_c,
                "accepted_amount_bdt_mill":  accepted_mill,
                "best_amount_bdt_mill":      accepted_mill if accepted_mill is not None else offered_mill,
                "weighted_avg_yield_pct":   wt_yield,
                "outflow_status":           status,
                "resulting_isin":           resulting_isin,
                "event_type":               "AUCTION",
                "source":                   str(yaml_path.name),
                "data_quality":             DQ_OK if settlement_date else "MISSING_SETTLEMENT",
            })

    return events


def load_all_auctions(holiday_set: Set[date]) -> list[dict]:
    """Load all auction events (T-Bill + Bond + FRTB)."""
    return load_tbill_auctions(holiday_set) + load_bond_auctions(holiday_set)
