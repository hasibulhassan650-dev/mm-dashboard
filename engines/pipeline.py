"""
engines/pipeline.py — Full daily ETL pipeline.

Run order:
  1. Load holidays into calendar_utils
  2. Fetch + parse GSOM pages
  3. Upsert securities and MTM snapshots
  4. Generate maturity and coupon events
  5. Fetch auction data from seed
  6. Generate auction settlement events
  7. Aggregate into daily_net_flow
  8. Persist everything to SQLite
"""
import datetime
import logging
import json
from typing import List

from config import (
    GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL, SEEDS_DIR
)
import calendar_utils
from db import (
    init_db, get_session,
    Security, MtmSnapshot, CouponEvent, MaturityEvent,
    AuctionEvent, HolidayCalendar, DailyNetFlow
)
from fetchers.gsom import fetch_gsom_html
from fetchers.auction import fetch_auction_rows
from parsers.securities import (
    parse_tbond_or_frtb, parse_tbill, validate_rows
)
from engines.inflow import generate_maturity_event, generate_coupon_schedule
from engines.outflow import generate_auction_event, generate_all_auction_events
from engines.aggregation import build_daily_flows

log = logging.getLogger(__name__)


def load_holidays_from_db(session) -> None:
    """Load holidays from DB into calendar_utils in-memory set."""
    rows = session.query(HolidayCalendar).all()
    dates = {r.calendar_date for r in rows}
    calendar_utils.load_holidays(dates)
    log.info("Loaded %d holidays from DB", len(dates))


def _upsert_security(session, row: dict) -> None:
    """Insert or update a security master record."""
    existing = session.get(Security, row["isin"])
    if existing:
        # Update mutable fields if this snapshot is newer
        if (row.get("settlement_date") or datetime.date.min) >= (existing.source_settlement_date or datetime.date.min):
            existing.outstanding_bdt_mill   = row.get("outstanding_bdt_mill")
            existing.source_settlement_date = row.get("settlement_date")
            existing.data_quality           = row.get("data_quality", "OK")
            import datetime as dt
            existing.last_updated_utc       = dt.datetime.utcnow()
    else:
        import datetime as dt
        session.add(Security(
            isin                   = row["isin"],
            security_name_raw      = row.get("security_name_raw"),
            security_name_norm     = row.get("security_name_norm"),
            security_type          = row.get("security_type"),
            issue_date             = row.get("issue_date"),
            maturity_date          = row.get("maturity_date"),
            coupon_rate_pct        = row.get("coupon_rate_pct"),
            coupon_frequency       = row.get("coupon_frequency"),
            issue_price            = row.get("issue_price"),
            outstanding_bdt_mill   = row.get("outstanding_bdt_mill"),
            source_page            = row.get("source_page"),
            source_settlement_date = row.get("settlement_date"),
            data_quality           = row.get("data_quality", "OK"),
            last_updated_utc       = dt.datetime.utcnow(),
        ))


def _upsert_maturity(session, event: dict) -> None:
    from sqlalchemy import delete
    session.execute(
        delete(MaturityEvent).where(MaturityEvent.isin == event["isin"])
    )
    session.add(MaturityEvent(**{
        k: v for k, v in event.items()
        if k in MaturityEvent.__table__.columns.keys()
    }))


def _upsert_coupon(session, event: dict) -> None:
    from sqlalchemy import select
    stmt = select(CouponEvent).where(
        CouponEvent.isin           == event["isin"],
        CouponEvent.scheduled_date == event["scheduled_date"],
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing:
        existing.amount_bdt_mill           = event["amount_bdt_mill"]
        existing.payment_date              = event["payment_date"]
        existing.outstanding_used_bdt_mill = event["outstanding_used_bdt_mill"]
        existing.formula_string            = event["formula_string"]
        existing.data_quality              = event["data_quality"]
    else:
        session.add(CouponEvent(**{
            k: v for k, v in event.items()
            if k in CouponEvent.__table__.columns.keys()
        }))


def _upsert_auction(session, event: dict) -> None:
    from sqlalchemy import select
    stmt = select(AuctionEvent).where(
        AuctionEvent.fiscal_year  == event["fiscal_year"],
        AuctionEvent.auction_no   == event["auction_no"],
        AuctionEvent.tenor_label  == event["tenor_label"],
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing:
        # Only update offered if not yet confirmed
        if existing.outflow_status != "CONFIRMED":
            existing.offered_amount_bdt_crore = event["offered_amount_bdt_crore"]
            existing.offered_amount_bdt_mill  = event["offered_amount_bdt_mill"]
            existing.settlement_date          = event["settlement_date"]
    else:
        session.add(AuctionEvent(**{
            k: v for k, v in event.items()
            if k in AuctionEvent.__table__.columns.keys()
        }))


def run_pipeline(
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
) -> dict:
    """
    Run the full ETL pipeline.
    Returns a summary dict with counts and any errors.
    """
    init_db()
    session = get_session()
    summary = {"securities": 0, "coupons": 0, "maturities": 0,
               "auctions": 0, "daily_rows": 0, "errors": []}

    try:
        # ── Step 1: holidays ──────────────────────────────────────────────────
        load_holidays_from_db(session)

        # ── Step 2 & 3: fetch and parse GSOM ─────────────────────────────────
        all_rows = []
        for stype, url in [("T_BOND", GSOM_TBOND_URL),
                           ("FRTB",   GSOM_FRTB_URL),
                           ("T_BILL", GSOM_TBILL_URL)]:
            try:
                result = fetch_gsom_html(stype)
                html   = result.get("html", "")
                src    = result["source_url"]
                if stype == "T_BILL":
                    rows = parse_tbill(html, src)
                else:
                    rows = parse_tbond_or_frtb(html, src, stype)
                rows = validate_rows(rows)
                all_rows.extend(rows)
                for row in rows:
                    _upsert_security(session, row)
            except Exception as exc:
                msg = f"GSOM fetch failed for {stype}: {exc}"
                log.error(msg)
                summary["errors"].append(msg)

        session.commit()
        summary["securities"] = len(all_rows)
        log.info("Upserted %d securities", len(all_rows))

        # ── Step 4: generate maturity and coupon events ───────────────────────
        all_coupon_events   = []
        all_maturity_events = []

        for row in all_rows:
            mat = generate_maturity_event(row)
            if mat:
                _upsert_maturity(session, mat)
                all_maturity_events.append(mat)

            coupons = generate_coupon_schedule(row)
            for c in coupons:
                _upsert_coupon(session, c)
            all_coupon_events.extend(coupons)

        session.commit()
        summary["maturities"] = len(all_maturity_events)
        summary["coupons"]    = len(all_coupon_events)

        # ── Step 5 & 6: auction events ────────────────────────────────────────
        raw_auctions = fetch_auction_rows(date_from, date_to)
        all_auction_events = generate_all_auction_events(raw_auctions)
        for evt in all_auction_events:
            _upsert_auction(session, evt)
        session.commit()
        summary["auctions"] = len(all_auction_events)

        # ── Step 7: aggregate ─────────────────────────────────────────────────
        if date_from is None:
            date_from = datetime.date.today() - datetime.timedelta(days=60)
        if date_to is None:
            date_to   = datetime.date.today() + datetime.timedelta(days=180)

        daily_rows = build_daily_flows(
            all_coupon_events, all_maturity_events,
            all_auction_events, date_from, date_to
        )

        # ── Step 8: persist daily_net_flow ────────────────────────────────────
        from sqlalchemy import delete
        session.execute(
            delete(DailyNetFlow).where(
                DailyNetFlow.flow_date >= date_from,
                DailyNetFlow.flow_date <= date_to,
            )
        )
        for dr in daily_rows:
            session.add(DailyNetFlow(**{
                k: v for k, v in dr.items()
                if k in DailyNetFlow.__table__.columns.keys()
            }))
        session.commit()
        summary["daily_rows"] = len(daily_rows)
        log.info("Pipeline complete: %s", summary)

    except Exception as exc:
        session.rollback()
        summary["errors"].append(f"FATAL: {exc}")
        log.exception("Pipeline failed")
    finally:
        session.close()

    return summary
