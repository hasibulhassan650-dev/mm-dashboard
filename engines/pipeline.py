"""
engines/pipeline.py — Full daily ETL pipeline.

Every run:
  1. Load holidays from DB into memory
  2. Fetch ALL three GSOM pages (exhaustive live fetch)
  3. Upsert every security seen — new ISINs added, existing ones updated
  4. Regenerate maturity + coupon events for EVERY security in the DB
     (not just today's fetch — so already-matured ISINs still contribute)
  5. Fetch auction rows from seed CSV
  6. Compute settlement dates and upsert auction events
  7. Re-query ALL events from DB for the date range
  8. Aggregate into daily_net_flow and persist
"""
import datetime
import logging
from typing import List

import calendar_utils
from config import GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL
from db import (
    init_db, get_session,
    Security, CouponEvent, MaturityEvent,
    AuctionEvent, HolidayCalendar, DailyNetFlow, MtmSnapshot,
    PrimaryYieldSnapshot, OMOTransaction,
)
from fetchers.gsom import fetch_gsom_html
from fetchers.auction_live import fetch_live_auction_rows as fetch_auction_rows
from fetchers.treasury import fetch_primary_yields, fetch_primary_yields_history
from parsers.securities import parse_tbond_or_frtb, parse_tbill, validate_rows
from engines.inflow import generate_maturity_event, generate_coupon_schedule
from engines.outflow import generate_all_auction_events
from engines.aggregation import build_daily_flows

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_holidays(session):
    rows = session.query(HolidayCalendar).all()
    calendar_utils.load_holidays({r.calendar_date for r in rows})
    log.info("Loaded %d holidays", len(rows))


def _upsert_security(session, row: dict):
    """Insert new security or update outstanding/quality on existing one."""
    existing = session.get(Security, row["isin"])
    now = datetime.datetime.utcnow()

    if existing:
        snap_date     = row.get("settlement_date") or datetime.date.min
        existing_date = existing.source_settlement_date or datetime.date.min
        if snap_date >= existing_date:
            existing.outstanding_bdt_mill   = row.get("outstanding_bdt_mill")
            existing.source_settlement_date = row.get("settlement_date")
            existing.data_quality           = row.get("data_quality", "OK")
            existing.last_updated_utc       = now
            # Also update coupon fields in case of re-issue with new rate
            if row.get("coupon_rate_pct") is not None:
                existing.coupon_rate_pct    = row["coupon_rate_pct"]
            if row.get("coupon_frequency") not in (None, "NONE"):
                existing.coupon_frequency   = row["coupon_frequency"]
    else:
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
            last_updated_utc       = now,
        ))


def _upsert_maturity(session, event: dict):
    """Replace maturity event for an ISIN (one per ISIN, always fresh)."""
    from sqlalchemy import delete
    session.execute(delete(MaturityEvent).where(MaturityEvent.isin == event["isin"]))
    session.add(MaturityEvent(**{
        k: v for k, v in event.items()
        if k in MaturityEvent.__table__.columns.keys()
    }))


def _upsert_coupon(session, event: dict):
    """Insert coupon event, or update it.

    FUTURE coupons are fully re-projected from today's outstanding and rate.
    PAST coupons are frozen: an FRTB's historical payments were fixed at their
    own period's reset rate, and a re-opened bond's past coupons used the
    then-outstanding — regenerating either with today's values rewrites
    history (this is how BD0927141038 drifted: amount was rewritten with the
    new 11.67% while coupon_rate_used_pct kept the old 11.72%).
    Self-heal: if a past row is internally inconsistent (that partial-update
    artifact), restore the amount from its own recorded rate and outstanding.
    """
    from sqlalchemy import select
    stmt = select(CouponEvent).where(
        CouponEvent.isin           == event["isin"],
        CouponEvent.scheduled_date == event["scheduled_date"],
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is None:
        session.add(CouponEvent(**{
            k: v for k, v in event.items()
            if k in CouponEvent.__table__.columns.keys()
        }))
        return

    existing.payment_date = event["payment_date"]
    if event["scheduled_date"] >= datetime.date.today():
        existing.amount_bdt_mill           = event["amount_bdt_mill"]
        existing.coupon_rate_used_pct      = event["coupon_rate_used_pct"]
        existing.outstanding_used_bdt_mill = event["outstanding_used_bdt_mill"]
        existing.formula_string            = event["formula_string"]
        existing.calc_method               = event["calc_method"]
        existing.data_quality              = event["data_quality"]
    elif (str(existing.calc_method or "").startswith("APPROX_")
          and existing.outstanding_used_bdt_mill and existing.coupon_rate_used_pct):
        div = 2 if str(existing.calc_method).endswith("HFLY") else 4
        expected = existing.outstanding_used_bdt_mill * (existing.coupon_rate_used_pct / 100) / div
        if abs((existing.amount_bdt_mill or 0) - expected) > max(0.5, expected * 0.001):
            log.info("Coupon self-heal %s %s: amount %.4f -> %.4f (from recorded rate %.4f%%)",
                     event["isin"], event["scheduled_date"],
                     existing.amount_bdt_mill or 0, expected, existing.coupon_rate_used_pct)
            existing.amount_bdt_mill = round(expected, 4)
            existing.formula_string  = (
                f"{existing.outstanding_used_bdt_mill:,.2f} × "
                f"({existing.coupon_rate_used_pct}/100) / {div}"
                f" = {expected:,.4f} [{existing.calc_method}] (restored from recorded rate)"
            )


def _upsert_auction(session, event: dict):
    """Raw SQL insert/update — bypasses SQLAlchemy type coercion entirely.
    auction_no stored as TEXT always: '44' for bills, 'B38' for bonds."""
    from sqlalchemy import text

    fy      = str(event.get("fiscal_year") or "")
    ano     = str(event.get("auction_no")  or "")
    tenor   = str(event.get("tenor_label") or "")
    ad      = str(event.get("auction_date") or "")
    sd      = str(event.get("settlement_date") or "")
    stype   = str(event.get("security_type") or "")
    oc      = float(event.get("offered_amount_bdt_crore") or 0)
    om      = float(event.get("offered_amount_bdt_mill")  or 0)
    ac      = event.get("accepted_amount_bdt_crore")
    am      = event.get("accepted_amount_bdt_mill")
    status  = str(event.get("outflow_status") or "PLANNED")
    rd      = int(event.get("roll_days") or 0)
    rr      = str(event.get("roll_reason") or event.get("adjustment_reason") or "")
    src     = str(event.get("source") or "")
    dq      = str(event.get("data_quality") or "OK")

    existing = session.execute(
        text("SELECT outflow_status FROM auction_events WHERE fiscal_year=:fy AND auction_no=:ano AND tenor_label=:tenor AND auction_date=:ad"),
        {"fy": fy, "ano": ano, "tenor": tenor, "ad": ad}
    ).fetchone()

    if existing:
        if existing[0] != "CONFIRMED":
            session.execute(text("""
                UPDATE auction_events SET
                  offered_amount_bdt_crore=:oc, offered_amount_bdt_mill=:om,
                  settlement_date=:sd, roll_days=:rd, roll_reason=:rr
                WHERE fiscal_year=:fy AND auction_no=:ano AND tenor_label=:tenor AND auction_date=:ad
            """), {"oc":oc,"om":om,"sd":sd,"rd":rd,"rr":rr,"fy":fy,"ano":ano,"tenor":tenor,"ad":ad})
    else:
        session.execute(text("""
            INSERT INTO auction_events
              (fiscal_year,auction_no,auction_date,settlement_date,security_type,
               tenor_label,offered_amount_bdt_crore,offered_amount_bdt_mill,
               accepted_amount_bdt_crore,accepted_amount_bdt_mill,
               outflow_status,roll_days,roll_reason,source,data_quality)
            VALUES
              (:fy,:ano,:ad,:sd,:st,:tl,:oc,:om,:ac,:am,:os,:rd,:rr,:src,:dq)
        """), {"fy":fy,"ano":ano,"ad":ad,"sd":sd,"st":stype,"tl":tenor,
               "oc":oc,"om":om,"ac":ac,"am":am,"os":status,
               "rd":rd,"rr":rr,"src":src,"dq":dq})


def _upsert_primary_yield(session, row: dict):
    """Insert or update a primary yield point (unique per tenor + auction_date)."""
    from sqlalchemy import select
    now = datetime.datetime.utcnow()
    existing = session.execute(
        select(PrimaryYieldSnapshot).where(
            PrimaryYieldSnapshot.tenor_label  == row["tenor_label"],
            PrimaryYieldSnapshot.auction_date == row["auction_date"],
        )
    ).scalar_one_or_none()
    if existing:
        existing.snapshot_date      = row["snapshot_date"]
        existing.cutoff_yield_pct   = row["cutoff_yield_pct"]
        existing.offered_bdt_crore  = row.get("offered_bdt_crore")
        existing.accepted_bdt_crore = row.get("accepted_bdt_crore")
        existing.ingested_utc       = now
    else:
        session.add(PrimaryYieldSnapshot(
            snapshot_date      = row["snapshot_date"],
            auction_date       = row["auction_date"],
            security_type      = row["security_type"],
            tenor_label        = row["tenor_label"],
            tenor_years        = row.get("tenor_years"),
            cutoff_yield_pct   = row["cutoff_yield_pct"],
            offered_bdt_crore  = row.get("offered_bdt_crore"),
            accepted_bdt_crore = row.get("accepted_bdt_crore"),
            source             = row.get("source", ""),
            ingested_utc       = now,
        ))


def _upsert_mtm_snapshot(session, row: dict):
    """Insert or refresh MtmSnapshot for the GSOM row's settlement date."""
    from sqlalchemy import select
    isin  = row["isin"]
    sdate = row.get("settlement_date")
    now   = datetime.datetime.utcnow()

    existing = session.execute(
        select(MtmSnapshot).where(
            MtmSnapshot.isin            == isin,
            MtmSnapshot.settlement_date == sdate,
        )
    ).scalar_one_or_none()

    if existing:
        existing.market_yield_pct        = row.get("market_yield_pct")
        existing.market_price            = row.get("market_price")
        existing.outstanding_bdt_mill    = row.get("outstanding_bdt_mill")
        existing.remaining_maturity_raw  = row.get("remaining_maturity_raw")
        existing.remaining_maturity_val  = row.get("remaining_maturity_val")
        existing.remaining_maturity_unit = row.get("remaining_maturity_unit")
        existing.last_coupon_date        = row.get("last_coupon_date")
        existing.next_coupon_date        = row.get("next_coupon_date")
        existing.ingested_utc            = now
    else:
        session.add(MtmSnapshot(
            isin                    = isin,
            settlement_date         = sdate,
            yield_date              = row.get("yield_date"),
            market_yield_pct        = row.get("market_yield_pct"),
            market_price            = row.get("market_price"),
            outstanding_bdt_mill    = row.get("outstanding_bdt_mill"),
            remaining_maturity_raw  = row.get("remaining_maturity_raw"),
            remaining_maturity_val  = row.get("remaining_maturity_val"),
            remaining_maturity_unit = row.get("remaining_maturity_unit"),
            last_coupon_date        = row.get("last_coupon_date"),
            next_coupon_date        = row.get("next_coupon_date"),
            issue_date_raw          = row.get("issue_date_raw"),
            maturity_date_raw       = row.get("maturity_date_raw"),
            source_page             = row.get("source_page"),
            source_row_index        = int(row["source_row_index"]) if row.get("source_row_index") is not None else None,
            data_quality            = row.get("data_quality", "OK"),
            ingested_utc            = now,
        ))


def _security_to_row(sec) -> dict:
    """Convert a Security ORM object back to the dict format used by inflow engine."""
    return {
        "isin":                 sec.isin,
        "security_name_raw":    sec.security_name_raw,
        "security_name_norm":   sec.security_name_norm,
        "security_type":        sec.security_type,
        "issue_date":           sec.issue_date,
        "maturity_date":        sec.maturity_date,
        "coupon_rate_pct":      sec.coupon_rate_pct,
        "coupon_frequency":     sec.coupon_frequency or "NONE",
        "issue_price":          sec.issue_price,
        "outstanding_bdt_mill": sec.outstanding_bdt_mill,
        "source_page":          sec.source_page,
        "settlement_date":      sec.source_settlement_date,
        "data_quality":         sec.data_quality or "OK",
        # Coupon anchor dates — not stored on Security; will be None for old records
        # The coupon engine handles None gracefully (uses maturity-anchored fallback)
        "last_coupon_date":     None,
        "next_coupon_date":     None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# GSOM fetch — exhaustive, all three pages
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_all_gsom_pages() -> list:
    """
    Fetch and parse all three GSOM MTM pages.
    Returns a flat list of validated security dicts.
    Errors on individual pages are logged but do not abort the whole run.
    """
    all_rows = []
    pages = [
        ("T_BOND", GSOM_TBOND_URL, False),
        ("FRTB",   GSOM_FRTB_URL,  False),
        ("T_BILL", GSOM_TBILL_URL,  True),
    ]

    for stype, url, is_bill in pages:
        try:
            result = fetch_gsom_html(stype)
            html   = result.get("html", "")
            src    = result["source_url"]

            if not html:
                log.warning("Empty HTML response for %s", stype)
                continue

            rows = parse_tbill(html, src) if is_bill else parse_tbond_or_frtb(html, src, stype)
            rows = validate_rows(rows)

            log.info("GSOM %s: fetched %d rows from %s", stype, len(rows), src)
            all_rows.extend(rows)

        except Exception as exc:
            log.error("GSOM fetch/parse failed for %s: %s", stype, exc, exc_info=True)

    log.info("GSOM total: %d securities fetched across all pages", len(all_rows))
    return all_rows


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE

def _prior_free_tuesday(session, d: datetime.date, max_back: int = 10):
    """The most recent working Tuesday strictly before `d` that holds NO OMO
    rows — where a CB Repo mislabelled onto a later 'as on' date really belongs
    (CB Repo is the Tuesday operation). Returns None if the nearest prior Tuesday
    is a holiday or already has data (don't guess when it isn't clean)."""
    import calendar_utils
    t = d - datetime.timedelta(days=1)
    for _ in range(max_back):
        if t.weekday() == 1:                       # Tuesday
            if not calendar_utils.is_working_day(t):
                return None                        # Tuesday was a holiday — don't guess
            has = session.query(OMOTransaction).filter_by(transaction_date=t).first()
            return t if has is None else None      # only if that Tuesday is empty
        t -= datetime.timedelta(days=1)
    return None


def _store_omo_txns(session, txns: list, now: datetime.datetime) -> tuple:
    """
    Store OMO rows with correction-aware supersession, returning (saved,
    superseded). For each operation ('as on') date, only the LATEST-published
    release is authoritative — this is how BB's GENUINE corrections work: a later
    press release re-states the SAME operation with the right figures. Naively
    keying on date alone would pile both onto one day.

    But two releases sharing an 'as on' date are NOT always a correction. When
    one carries CB_REPO and the other does not, they are DIFFERENT operations BB
    double-dated — the Aug-2026 case where the 04-Aug (Tuesday) CB Repo, stamped
    'as on 06 August' like the genuine 06-Aug operation, was silently deleted by
    supersession. The mislabel guard below re-homes the CB_REPO operation to the
    Tuesday it belongs to (on either fetch order / within one batch) instead of
    dropping it. Extracted from run_omo_fetch so it is unit-testable.
    """
    from collections import defaultdict

    def _pub(x) -> datetime.date:
        return x.get("source_pub_date") or datetime.date.min

    def _insert(r):
        session.add(OMOTransaction(
            transaction_date   = r["transaction_date"],
            maturity_date      = r["maturity_date"],
            instrument         = r["instrument"],
            tenor_label        = r["tenor_label"],
            tenor_days         = r["tenor_days"],
            accepted_bdt_crore = r["accepted_bdt_crore"],
            maturity_bdt_crore = r.get("maturity_bdt_crore"),
            rate_pct           = r.get("rate_pct"),
            rate_range         = r.get("rate_range"),
            direction          = r["direction"],
            source_pdf         = r.get("source_pdf"),
            source_pub_date    = r.get("source_pub_date"),
            source_serial      = r.get("source_serial"),
            ingested_utc       = now,
        ))

    by_date: dict = defaultdict(list)
    for r in txns:
        by_date[r["transaction_date"]].append(r)

    saved = superseded = 0
    for d, rows in by_date.items():
        latest_pub = max((_pub(x) for x in rows), default=datetime.date.min)
        keep = [x for x in rows if _pub(x) == latest_pub]   # drop older release if both in batch
        if not keep:
            continue

        # Within-batch mislabel: if this one fetch pulled BOTH releases for date d
        # and the OLDER one carries a CB Repo the latest lacks, the keep-filter
        # above would silently drop it. That CB Repo is a different (Tuesday)
        # operation BB double-dated — re-home the whole older release to its
        # Tuesday instead of losing it. (Cross-run duplicates are caught below.)
        if not any(k["instrument"] == "CB_REPO" for k in keep):
            older_cb_pub = max((_pub(x) for x in rows
                                if _pub(x) != latest_pub and x["instrument"] == "CB_REPO"),
                               default=datetime.date.min)
            if older_cb_pub != datetime.date.min:
                target = _prior_free_tuesday(session, d)
                if target is not None:
                    for x in (r for r in rows if _pub(r) == older_cb_pub):
                        x2 = dict(x)
                        x2["transaction_date"] = target
                        x2["maturity_date"]    = target + datetime.timedelta(days=(x.get("tenor_days") or 0))
                        _insert(x2); saved += 1
                    log.error("OMO mislabel guard (batch): CB_REPO operation re-homed from 'as on "
                              "%s' to Tuesday %s — BB double-dated two operations. CONFIRM %s was a "
                              "working day (not a bank holiday).", d, target, target)

        existing = session.query(OMOTransaction).filter_by(transaction_date=d).all()
        stored_pub = max((e.source_pub_date for e in existing if e.source_pub_date), default=None)
        inc_pub = None if latest_pub == datetime.date.min else latest_pub

        # ── OMO mislabel guard: two operations stamped with one 'as on' date ──
        # A genuine BB correction re-states the SAME operation (it keeps CB_REPO).
        # When one release for this date carries CB_REPO and the other does NOT,
        # they are DIFFERENT operations BB gave the same 'as on' date — the
        # Aug-2026 bug where the 04-Aug Tuesday CB Repo, mislabelled 'as on 06
        # August', was silently deleted by the genuine 06-Aug release. Re-home the
        # CB_REPO release to the Tuesday it belongs to, on EITHER fetch order,
        # instead of one side deleting or regressing the other. Tight + reversible:
        # only fires when exactly one side has CB Repo and the prior Tuesday is a
        # clean, empty working day. Loud, so the desk can confirm the date.
        if existing and inc_pub is not None and stored_pub is not None and inc_pub != stored_pub:
            stored_has_cb = any(e.instrument == "CB_REPO" for e in existing)
            inc_has_cb    = any(r["instrument"] == "CB_REPO" for r in keep)
            if stored_has_cb != inc_has_cb:
                target = _prior_free_tuesday(session, d)
                if target is not None:
                    if stored_has_cb:      # keep incoming on d, move stored CB Repo op to its Tuesday
                        for e in existing:
                            e.transaction_date = target
                            e.maturity_date    = target + datetime.timedelta(days=e.tenor_days or 0)
                        session.flush()
                        for r in keep:
                            _insert(r); saved += 1
                    else:                  # keep stored on d, store incoming CB Repo op on its Tuesday
                        for r in keep:
                            r2 = dict(r)
                            r2["transaction_date"] = target
                            r2["maturity_date"]    = target + datetime.timedelta(days=(r.get("tenor_days") or 0))
                            _insert(r2); saved += 1
                    log.error("OMO mislabel guard: CB_REPO operation re-homed from 'as on %s' to "
                              "Tuesday %s — BB double-dated two operations. CONFIRM %s was a "
                              "working day (not a bank holiday).", d, target, target)
                    continue

        # Replace the date's rows when the incoming release is NEWER (a
        # correction supersedes), or SAME publication but re-parsed into at least
        # as many rows (a parser fix must be able to overwrite already-stored
        # wrong labels — e.g. MLS/SDF that were mislabelled as IBLF — without a
        # partial parse ever shrinking good data).
        newer = inc_pub is not None and (stored_pub is None or inc_pub > stored_pub)
        same_reparse = inc_pub is not None and stored_pub is not None and inc_pub == stored_pub and len(keep) >= len(existing)
        replace = existing and (newer or same_reparse)
        if replace:
            if stored_pub is not None and inc_pub is not None and inc_pub > stored_pub:
                superseded += 1
                log.warning("OMO supersede %s: release pub %s replaces older pub %s (%d rows)",
                            d, inc_pub, stored_pub, len(existing))
            for e in existing:
                session.delete(e)
            session.flush()
            for r in keep:
                _insert(r); saved += 1
        elif not existing:
            for r in keep:
                _insert(r); saved += 1
        elif inc_pub is not None and stored_pub is not None and inc_pub < stored_pub:
            # Incoming release is OLDER than what we already have for this date
            # (e.g. re-seeing the mislabelled 343 after the 345 correction is
            # stored) — never regress: add nothing, remove nothing.
            continue
        else:
            # Same publication (or legacy rows w/o pub info): row-level upsert so
            # genuinely new lines are added and fields backfilled.
            for r in keep:
                ex = session.query(OMOTransaction).filter_by(
                    transaction_date=r["transaction_date"], instrument=r["instrument"],
                    tenor_days=r["tenor_days"], accepted_bdt_crore=r["accepted_bdt_crore"],
                ).first()
                if ex:
                    if ex.source_pub_date is None and r.get("source_pub_date"):
                        ex.source_pub_date = r["source_pub_date"]; ex.source_serial = r.get("source_serial")
                    if ex.maturity_bdt_crore is None and r.get("maturity_bdt_crore"):
                        ex.maturity_bdt_crore = r["maturity_bdt_crore"]
                    if ex.rate_range is None and r.get("rate_range"):
                        ex.rate_range = r["rate_range"]
                else:
                    _insert(r); saved += 1
    return saved, superseded


def run_omo_fetch(days_back: int = 28, max_files: int = 20) -> dict:
    """Fetch last N OMO press release PDFs and store transactions to DB."""
    from fetchers.omo import fetch_omo_data
    init_db()
    session = get_session()
    try:
        txns = fetch_omo_data(days_back=days_back, max_files=max_files)
        saved, superseded = _store_omo_txns(session, txns, datetime.datetime.utcnow())
        session.commit()
        log.info("OMO fetch: %d rows stored, %d date(s) superseded by corrections, from %d PDFs",
                 saved, superseded, len(txns))
        # Grand-total reconciliation failures (set by the parser) → run errors, so
        # they surface on the dashboard's last_run_errors and to the watchdog.
        recon = sorted({t["recon_mismatch"] for t in txns if t.get("recon_mismatch")})
        return {"rows": saved, "pdfs": max_files, "errors": recon}
    except Exception as exc:
        session.rollback()
        log.exception("OMO fetch failed")
        return {"rows": 0, "pdfs": 0, "errors": [str(exc)]}
    finally:
        session.close()


def run_primary_yield_history(months_back: int = 8) -> dict:
    """Fetch last N months of primary yield data from BB Treasury and store to DB."""
    init_db()
    session = get_session()
    try:
        rows = fetch_primary_yields_history(months_back=months_back)
        for r in rows:
            _upsert_primary_yield(session, r)
        session.commit()
        log.info("Primary yield history: stored %d rows (%d months)", len(rows), months_back)
        return {"rows": len(rows), "months": months_back, "errors": []}
    except Exception as exc:
        session.rollback()
        log.exception("Primary yield history failed")
        return {"rows": 0, "months": months_back, "errors": [str(exc)]}
    finally:
        session.close()


# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    date_from: datetime.date = None,
    date_to:   datetime.date = None,
) -> dict:
    """
    Run the full ETL pipeline. Safe to call every day.
    - Fetches live data from all 3 GSOM pages every single run
    - Captures every ISIN currently on the pages
    - Adds new ISINs automatically
    - Regenerates all events for all ISINs in DB (not just today's fetch)
    - Aggregates net flow for the full date range
    Returns summary dict.
    """
    init_db()
    session = get_session()
    summary = {
        "securities_fetched": 0,
        "securities_in_db":   0,
        "securities":         0,
        "coupons":            0,
        "maturities":         0,
        "auctions":           0,
        "daily_rows":         0,
        "errors":             [],
    }

    try:
        # ── 1. Holidays ───────────────────────────────────────────────────────
        _load_holidays(session)

        # ── 2. Fetch all GSOM pages (live, every run) ─────────────────────────
        fetched_rows = _fetch_all_gsom_pages()
        summary["securities_fetched"] = len(fetched_rows)

        if not fetched_rows:
            summary["errors"].append("All GSOM pages returned empty — check connectivity")
            # Don't abort: we can still regenerate events from existing DB records

        # ── 3. Upsert every fetched security ──────────────────────────────────
        # Also store next_coupon_date and last_coupon_date on the Security row
        # so the event generator can use them when re-reading from DB
        for row in fetched_rows:
            _upsert_security(session, row)
            _upsert_mtm_snapshot(session, row)
        session.commit()

        # Store coupon anchor dates separately so we can restore them later
        # Map: isin -> (last_coupon_date, next_coupon_date)
        coupon_anchors = {
            row["isin"]: (row.get("last_coupon_date"), row.get("next_coupon_date"))
            for row in fetched_rows
            if row.get("next_coupon_date") is not None
        }

        # ── 4. Regenerate events for ALL securities in DB ─────────────────────
        # This is the critical step: we process every ISIN ever seen, not just
        # what was on the page today. ISINs that matured last week are still in
        # the DB and still need their maturity event on the correct date.
        all_securities = session.query(Security).all()
        summary["securities_in_db"] = len(all_securities)
        summary["securities"] = len(fetched_rows)

        log.info("Regenerating events for %d securities in DB", len(all_securities))

        all_coupon_events   = []
        all_maturity_events = []

        for sec in all_securities:
            row = _security_to_row(sec)

            # Restore coupon anchor dates from today's fetch if available
            if sec.isin in coupon_anchors:
                row["last_coupon_date"], row["next_coupon_date"] = coupon_anchors[sec.isin]

            # Maturity event
            mat = generate_maturity_event(row)
            if mat:
                _upsert_maturity(session, mat)
                all_maturity_events.append(mat)

            # Coupon schedule (empty for T-Bills)
            coupons = generate_coupon_schedule(row)
            for c in coupons:
                _upsert_coupon(session, c)
            all_coupon_events.extend(coupons)

        session.commit()
        summary["maturities"] = len(all_maturity_events)
        summary["coupons"]    = len(all_coupon_events)
        log.info("Events: %d maturities, %d coupon dates", len(all_maturity_events), len(all_coupon_events))

        # ── 5 & 6. Auction events ─────────────────────────────────────────────
        raw_auctions = fetch_auction_rows(date_from, date_to)
        auction_events = generate_all_auction_events(raw_auctions)
        for evt in auction_events:
            _upsert_auction(session, evt)
        session.commit()
        summary["auctions"] = len(auction_events)

        # Primary yield history is fetched separately via the sidebar button
        # (fetch_primary_yields_history) — not run here to keep pipeline fast.

        # ── 7. Set date range ─────────────────────────────────────────────────
        if date_from is None:
            date_from = datetime.date.today() - datetime.timedelta(days=60)
        if date_to is None:
            date_to   = datetime.date.today() + datetime.timedelta(days=365)

        # ── 8. Re-query ALL events from DB for the range ──────────────────────
        # Use DB as the source of truth, not the in-memory lists.
        # This ensures any events from previous runs are included too.
        # Filter by PAYMENT date, matching what build_daily_flows buckets on.
        # Filtering on scheduled_date while bucketing on payment_date would drop
        # an event scheduled just before the window but paid inside it, and vice
        # versa at the far end — a silent off-by-one at both range edges.
        db_coupons = session.query(CouponEvent).filter(
            CouponEvent.payment_date >= date_from,
            CouponEvent.payment_date <= date_to,
        ).all()
        db_maturities = session.query(MaturityEvent).filter(
            MaturityEvent.payment_date >= date_from,
            MaturityEvent.payment_date <= date_to,
        ).all()
        db_auctions = session.query(AuctionEvent).filter(
            AuctionEvent.settlement_date >= date_from,
            AuctionEvent.settlement_date <= date_to,
        ).all()

        # Convert ORM objects to dicts for aggregation engine
        coupon_dicts = [{
            "scheduled_date":      c.scheduled_date,
            "payment_date":        c.payment_date,
            "amount_bdt_mill":     c.amount_bdt_mill or 0.0,
            "isin":                c.isin,
            "data_quality":        c.data_quality,
        } for c in db_coupons]

        maturity_dicts = [{
            "scheduled_date":      m.scheduled_date,
            "payment_date":        m.payment_date,
            "principal_bdt_mill":  m.principal_bdt_mill or 0.0,
            "isin":                m.isin,
            "data_quality":        m.data_quality,
        } for m in db_maturities]

        auction_dicts = [{
            "settlement_date":             a.settlement_date,
            "offered_amount_bdt_mill":     a.offered_amount_bdt_mill or 0.0,
            "accepted_amount_bdt_mill":    a.accepted_amount_bdt_mill,
            "outflow_status":              a.outflow_status,
            "security_type":               a.security_type,
            "data_quality":                a.data_quality,
        } for a in db_auctions]

        log.info(
            "DB query for range %s→%s: %d coupons, %d maturities, %d auctions",
            date_from, date_to,
            len(coupon_dicts), len(maturity_dicts), len(auction_dicts)
        )

        # ── 9. Aggregate ──────────────────────────────────────────────────────
        daily_rows = build_daily_flows(
            coupon_dicts, maturity_dicts, auction_dicts,
            date_from, date_to,
        )

        # ── 10. Persist daily_net_flow (replace range) ────────────────────────
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

        log.info(
            "Pipeline complete | fetched=%d | db_securities=%d | "
            "maturities=%d | coupons=%d | auctions=%d | daily_rows=%d | errors=%d",
            summary["securities_fetched"],
            summary["securities_in_db"],
            summary["maturities"],
            summary["coupons"],
            summary["auctions"],
            summary["daily_rows"],
            len(summary["errors"]),
        )

    except Exception as exc:
        session.rollback()
        summary["errors"].append(f"FATAL: {exc}")
        log.exception("Pipeline failed with unhandled exception")
    finally:
        session.close()

    return summary
