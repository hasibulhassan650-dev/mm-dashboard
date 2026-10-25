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
    Security, CouponEvent, MaturityEvent, BuybackEvent, PressRelease,
    BgiibAuction, SukukOutstanding, SukukDocument,
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

# Marks an auction_events row that exists ONLY because a BB result row
# existed with no calendar slot to attach to. Written on creation and read
# back when the orphans are swept, so the two can never drift apart.
_UNCALENDARED = "BB treasury results (uncalendared auction)"


# ══════════════════════════════════════════════════════════════════════════════
# DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_holidays(session):
    rows = session.query(HolidayCalendar).all()
    calendar_utils.load_calendar_rows(rows)   # WORKING_DAY rows = working weekends
    log.info("Loaded %d calendar rows", len(rows))


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
    sc      = event.get("sale_value_bdt_crore")
    sm      = event.get("sale_value_bdt_mill")
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
               sale_value_bdt_crore,sale_value_bdt_mill,
               outflow_status,roll_days,roll_reason,source,data_quality)
            VALUES
              (:fy,:ano,:ad,:sd,:st,:tl,:oc,:om,:ac,:am,:sc,:sm,:os,:rd,:rr,:src,:dq)
        """), {"fy":fy,"ano":ano,"ad":ad,"sd":sd,"st":stype,"tl":tenor,
               "oc":oc,"om":om,"ac":ac,"am":am,"sc":sc,"sm":sm,"os":status,
               "rd":rd,"rr":rr,"src":src,"dq":dq})


def replace_daily_rows(session, model, rows: list, key_fields: tuple,
                       date_field: str = "trade_date") -> dict:
    """Make the DB match BB's page for every date the page returned.

    BB's call-money page is a RUNNING intraday table: the first hourly fetch of
    a day captures a partial total (14-Sep-2026 overnight: 2,148 cr / 35 deals
    stored, 6,100 cr / 81 deals final), and for a while the new day's header can
    sit over the previous day's rows. Insert-only storage froze every such
    snapshot for good (22 of the last 52 rows were wrong). So, per date on the
    page: update rows whose figures changed, insert new ones, delete ones the
    page no longer has. Dates absent from the page are left alone — a truncated
    response must never wipe history — and a date is only touched when the page
    holds at least one row for it.
    """
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r[date_field], []).append(r)
    stats = {"updated": 0, "inserted": 0, "deleted": 0, "unchanged": 0}
    for d, page_rows in by_date.items():
        if not page_rows:
            continue
        existing = session.query(model).filter(getattr(model, date_field) == d).all()
        ex_by_key = {tuple(getattr(e, k) for k in key_fields): e for e in existing}
        seen = set()
        for r in page_rows:
            key = tuple(r.get(k) for k in key_fields)
            seen.add(key)
            e = ex_by_key.get(key)
            if e is None:
                session.add(model(**r)); stats["inserted"] += 1
                continue
            changed = False
            for f, v in r.items():
                if f in ("ingested_utc", "id") or not hasattr(e, f):
                    continue
                if getattr(e, f) != v:
                    setattr(e, f, v); changed = True
            if changed:
                if hasattr(e, "ingested_utc") and "ingested_utc" in r:
                    e.ingested_utc = r["ingested_utc"]
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        for key, e in ex_by_key.items():
            if key not in seen:
                session.delete(e); stats["deleted"] += 1
    session.flush()
    return stats


def confirm_auctions_from_results(session) -> dict:
    """Attach BB's published auction RESULTS to the calendar auctions they settle.

    The calendar (auction_events) is a plan: an offered amount and a settlement
    date computed from the holiday table. The results page (primary_yield_
    snapshots) is what actually happened: the accepted amount and the ISSUE
    date BB settled on. Until Sep-2026 the two were never joined — every
    auction stayed PLANNED forever, so daily_net_flow booked the OFFERED
    amount on a COMPUTED date, even where BB accepted a different amount
    (devolvement / over-subscription) or moved the auction (the pre-Eid
    bills were issued 24-May, not the computed 01-Jun).

    Match, per tenor: (1) a calendar row on exactly the derived auction date;
    else (2) the nearest still-unmatched calendar row in the 10 days up to the
    issue date (BB shifted the auction — the pre-Eid bills ran on the working
    Saturday 23-May against a 24-May calendar slot). A result with NO row at
    all inside the calendar era is an auction BB added ad hoc (5Y on
    27-Nov-2025 and 03-Feb-2026): inserted as CONFIRMED so the outflow never
    misses an auction that printed. Each calendar row is claimed by at most
    one result, so a re-run re-applies exactly the same facts.

    Finally, an ad-hoc row whose result has since MOVED to a real calendar slot
    is swept: it would otherwise keep booking its outflow on a date BB never
    settled (see the orphan sweep below).
    """
    from sqlalchemy import text as _t
    from config import CRORE_TO_MILLION, fiscal_year
    res = session.execute(_t(
        "SELECT tenor_label, issue_date, auction_date, accepted_bdt_crore, cutoff_yield_pct, "
        "security_type, sale_value_bdt_crore FROM primary_yield_snapshots "
        "WHERE issue_date IS NOT NULL ORDER BY auction_date")).fetchall()
    cal = session.query(AuctionEvent).all()
    by_tenor: dict = {}
    for a in cal:
        by_tenor.setdefault(a.tenor_label, []).append(a)
    cal_start = min((a.auction_date for a in cal if a.auction_date), default=None)
    claimed: set = set()
    confirmed = moved = added = 0

    def _apply(a, issue, adate, acc, cutoff, sale=None):
        nonlocal confirmed, moved
        if acc is not None:
            a.accepted_amount_bdt_crore = float(acc)
            a.accepted_amount_bdt_mill  = round(float(acc) * CRORE_TO_MILLION, 4)
            a.outflow_status            = "CONFIRMED"
        if sale is not None:
            # The cash, beside the par amount. Both are kept: the ladder needs
            # the cash, and the face value is what BB allotted and what matures.
            a.sale_value_bdt_crore = float(sale)
            a.sale_value_bdt_mill  = round(float(sale) * CRORE_TO_MILLION, 4)
        if cutoff is not None:
            a.weighted_avg_yield_pct = float(cutoff)
        if a.settlement_date != issue:
            a.roll_reason = (f"Settled {issue} per BB results (calendar computed "
                             f"{a.settlement_date}; auction held {adate})")
            a.settlement_date = issue
            moved += 1
        confirmed += 1

    def _d(v):   # SQLite hands raw-SQL dates back as strings
        return datetime.date.fromisoformat(v[:10]) if isinstance(v, str) else v

    pending = []
    for tenor, issue, adate, acc, cutoff, stype, sale in res:
        issue, adate = _d(issue), _d(adate)
        exact = [a for a in by_tenor.get(tenor, []) if a.auction_date == adate and id(a) not in claimed]
        if exact:
            claimed.add(id(exact[0])); _apply(exact[0], issue, adate, acc, cutoff, sale)
        else:
            pending.append((tenor, issue, adate, acc, cutoff, stype, sale))
    for tenor, issue, adate, acc, cutoff, stype, sale in pending:
        cands = [a for a in by_tenor.get(tenor, [])
                 if a.auction_date is not None and id(a) not in claimed
                 and issue - datetime.timedelta(days=10) <= a.auction_date <= issue]
        if cands:
            a = max(cands, key=lambda x: x.auction_date)
            claimed.add(id(a)); _apply(a, issue, adate, acc, cutoff, sale)
            continue
        if cal_start is None or adate is None or adate < cal_start or acc is None:
            continue
        off = float(acc)   # target size unknown for an ad-hoc auction: plan = actual
        a = AuctionEvent(
            fiscal_year=fiscal_year(adate), auction_no=None, auction_date=adate,
            settlement_date=issue, security_type=stype, tenor_label=tenor,
            offered_amount_bdt_crore=off, offered_amount_bdt_mill=round(off * CRORE_TO_MILLION, 4),
            outflow_status="PLANNED", roll_days=0,
            roll_reason=f"Not in auction calendar; created from BB results (issued {issue})",
            source=_UNCALENDARED, data_quality="OK")
        session.add(a)
        by_tenor.setdefault(tenor, []).append(a)
        claimed.add(id(a)); _apply(a, issue, adate, acc, cutoff, sale)
        added += 1

    # An uncalendared row exists only because a result row existed. If no result
    # claims it after both passes, that result's dates have MOVED and its auction
    # has been re-homed onto a real calendar row -- leaving this one behind, still
    # CONFIRMED, still booking its accepted amount on its old settlement date.
    #
    # That is how 23-May-2026 came to hold two copies of the same 90,000 mn
    # pre-Eid auction. The yields row first read auction 21-May / issue 23-May,
    # because the calendar did not yet know 23-May was a declared working
    # Saturday; these rows were created from it; once the dates were corrected to
    # 23-May / 24-May the genuine calendar rows won the match and the invented
    # ones were never looked at again. build_daily_flows sums by settlement_date
    # without de-duplicating, so the next recompute covering May would have
    # booked a phantom second 9,000 crore outflow.
    #
    # Deleting is safe in the one way that matters: issue_date is NOT NULL under
    # a validated CHECK, so the results query above cannot omit a live result and
    # strand the row that belongs to it.
    orphans = [a for a in cal if a.source == _UNCALENDARED and id(a) not in claimed]
    for a in orphans:
        log.warning("Removing orphaned uncalendared auction: %s auctioned %s, settled %s, "
                    "%s mn -- no BB result points at it any more",
                    a.tenor_label, a.auction_date, a.settlement_date,
                    a.accepted_amount_bdt_mill)
        session.delete(a)

    session.flush()
    log.info("Auction results linked: %d calendar auctions confirmed, %d settlement dates "
             "corrected from BB issue dates, %d uncalendared auctions added, %d orphans removed",
             confirmed, moved, added, len(orphans))
    return {"confirmed": confirmed, "moved": moved, "added": added,
            "orphans_removed": len(orphans)}


def upsert_primary_yield(session, row: dict):
    """THE single supported way to write a yield row. Every writer must use it.

    It is the only path that stores `issue_date` (what BB actually prints) and
    keeps `auction_date` as the derived trading day. reconcile.py bypassed it
    with a hand-written INSERT and produced 32 bad rows over two weeks; the
    repo-scan test in tests/test_selfsustaining.py now fails CI if a second
    writer appears.
    """
    """Insert or update a primary yield point (unique per tenor + auction_date)."""
    from sqlalchemy import select
    now = datetime.datetime.utcnow()
    # issue_date is the column the whole auction/settlement join hangs off, and a
    # NULL one is the defect the Sep-2026 audit chased. If a caller cannot supply
    # it, derive it here (T+1 working day) rather than store a hole — that also
    # means the NOT NULL constraint in db.py can never block a legitimate write.
    if not row.get("issue_date") and row.get("auction_date"):
        try:
            row = dict(row)
            row["issue_date"] = calendar_utils.get_next_working_day(
                row["auction_date"] + datetime.timedelta(days=1))["result_date"]
        except Exception as exc:
            log.warning("could not derive issue_date for %s %s: %s",
                        row.get("tenor_label"), row.get("auction_date"), exc)

    existing = session.execute(
        select(PrimaryYieldSnapshot).where(
            PrimaryYieldSnapshot.tenor_label  == row["tenor_label"],
            PrimaryYieldSnapshot.auction_date == row["auction_date"],
        )
    ).scalar_one_or_none()

    # ONE AUCTION, ONE ROW — the invariant the partial unique index on
    # (auction_date, isin) enforces, and which this writer has to know about.
    # The table is keyed (tenor_label, auction_date), so a re-parse that labels
    # the same auction differently — a re-issue read from BB's remaining-
    # maturity column one day and its tenor-and-name column the next — looks
    # like a brand-new row and lands BESIDE the right one, double-counting it.
    # Since Oct-2026 the index refuses that insert, which turned the silent
    # duplicate into a crash that killed the whole treasury step. Neither is
    # acceptable: find the row by ISIN and CORRECT its label.
    if existing is None and row.get("isin"):
        by_isin = session.execute(
            select(PrimaryYieldSnapshot).where(
                PrimaryYieldSnapshot.isin         == row["isin"],
                PrimaryYieldSnapshot.auction_date == row["auction_date"],
            )
        ).scalar_one_or_none()
        if by_isin is not None:
            if by_isin.tenor_label != row["tenor_label"]:
                log.warning("yield row for %s on %s was stored as %s and BB now reads %s "
                            "— relabelling in place rather than adding a second row",
                            row["isin"], row["auction_date"], by_isin.tenor_label,
                            row["tenor_label"])
            existing = by_isin
    if existing:
        existing.snapshot_date      = row["snapshot_date"]
        existing.issue_date         = row.get("issue_date") or existing.issue_date
        # The label can legitimately change when the row was found by ISIN:
        # that is the relabelling above, and leaving it stale would keep the
        # old tenor on the curve for ever.
        existing.tenor_label        = row["tenor_label"]
        existing.tenor_years        = row.get("tenor_years", existing.tenor_years)
        existing.security_type      = row.get("security_type") or existing.security_type
        existing.cutoff_yield_pct   = row["cutoff_yield_pct"]
        existing.offered_bdt_crore  = row.get("offered_bdt_crore")
        existing.accepted_bdt_crore = row.get("accepted_bdt_crore")
        # Only overwrite the newer fields when this parse actually produced
        # them: an older BB layout yields None, and None must not wipe a value
        # a richer page already gave us.
        for src, dest in (("isin", "isin"),
                          ("reissue_remaining", "reissue_remaining"),
                          ("original_tenor_label", "original_tenor_label"),
                          ("original_tenor_years", "original_tenor_years"),
                          ("sale_value_bdt_crore", "sale_value_bdt_crore"),
                          ("bids_received_count", "bids_received_count"),
                          ("bids_accepted_count", "bids_accepted_count"),
                          ("yield_range_received", "yield_range_received"),
                          ("yield_range_accepted", "yield_range_accepted"),
                          ("weighted_avg_price", "weighted_avg_price"),
                          ("devolvement_yield_pct", "devolvement_yield_pct")):
            v = row.get(src)
            if v is not None:
                setattr(existing, dest, v)
        if row.get("is_reissue"):
            existing.is_reissue = True
        existing.ingested_utc       = now
    else:
        session.add(PrimaryYieldSnapshot(
            snapshot_date      = row["snapshot_date"],
            auction_date       = row["auction_date"],
            issue_date         = row.get("issue_date"),
            security_type      = row["security_type"],
            tenor_label        = row["tenor_label"],
            tenor_years        = row.get("tenor_years"),
            cutoff_yield_pct   = row["cutoff_yield_pct"],
            offered_bdt_crore  = row.get("offered_bdt_crore"),
            accepted_bdt_crore = row.get("accepted_bdt_crore"),
            isin                 = row.get("isin"),
            is_reissue           = bool(row.get("is_reissue")),
            reissue_remaining    = row.get("reissue_remaining"),
            original_tenor_label = row.get("original_tenor_label"),
            original_tenor_years = row.get("original_tenor_years"),
            sale_value_bdt_crore = row.get("sale_value_bdt_crore"),
            bids_received_count  = row.get("bids_received_count"),
            bids_accepted_count  = row.get("bids_accepted_count"),
            yield_range_received = row.get("yield_range_received"),
            yield_range_accepted = row.get("yield_range_accepted"),
            weighted_avg_price   = row.get("weighted_avg_price"),
            devolvement_yield_pct= row.get("devolvement_yield_pct"),
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


def _prior_gap_day(session, d: datetime.date, max_back: int = 12):
    """The most recent day before `d` where the money market TRADED (call money
    or reference rates printed) but NO OMO is recorded — exactly the day a
    mis-dated release belongs to. Bank holidays are skipped for free (nothing
    traded), and None is returned when the nearest prior working day already has
    OMO or nothing matches, so we never guess."""
    import calendar_utils
    from db import CallMoneyRate, RefRate
    t = d - datetime.timedelta(days=1)
    for _ in range(max_back):
        if calendar_utils.is_working_day(t):
            if session.query(OMOTransaction).filter_by(transaction_date=t).first():
                return None          # prior working day already covered — no gap
            traded = (session.query(CallMoneyRate).filter_by(trade_date=t).first()
                      or session.query(RefRate).filter_by(trade_date=t).first())
            if traded:
                return t             # market open + no OMO = the missing day
            # market shut that day (holiday) — keep walking back
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
    from sqlalchemy import text

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

    # Row-set signatures of every stored operation, for the duplicate-content
    # guard below. (instrument, tenor, accepted, maturity) per row — an entire
    # table copied verbatim onto another date matches exactly.
    def _sig(rs):
        return tuple(sorted((x[0], int(x[1] or 0), round(float(x[2] or 0), 2), round(float(x[3] or 0), 2))
                            for x in rs))
    stored_sigs: dict = {}
    for r in session.execute(text("SELECT transaction_date, instrument, tenor_days, accepted_bdt_crore, "
                                  "COALESCE(maturity_bdt_crore, 0) FROM omo_transactions")).fetchall():
        dd = datetime.date.fromisoformat(r[0][:10]) if isinstance(r[0], str) else r[0]
        stored_sigs.setdefault(dd, []).append(r[1:])
    stored_sigs = {dd: _sig(rs) for dd, rs in stored_sigs.items()}

    saved = superseded = 0
    for d, rows in by_date.items():
        latest_pub = max((_pub(x) for x in rows), default=datetime.date.min)
        keep = [x for x in rows if _pub(x) == latest_pub]   # drop older release if both in batch
        if not keep:
            continue

        # ── Duplicate-content guard: a release whose WHOLE table equals another
        # date's operation is a re-published stale file, not a new operation.
        # BB's "as on 04 May 2026" (pr14062) was the 05-Apr table verbatim; it
        # put a phantom 2,587 cr IBLF tranche into outstanding for 28 days. Never
        # store it (and never let a re-fetch overwrite the reconstruction).
        inc_sig = _sig([(x["instrument"], x.get("tenor_days"), x["accepted_bdt_crore"],
                         x.get("maturity_bdt_crore")) for x in keep])
        twin = next((dd for dd, sg in stored_sigs.items() if dd != d and sg == inc_sig and len(sg) >= 3), None)
        if twin is not None:
            log.error("OMO duplicate-content guard: release 'as on %s' (%s) is a verbatim copy of the "
                      "%s operation — BB re-published a stale table. NOT stored; the %s operation "
                      "must be reconstructed from BB's printed maturities.",
                      d, keep[0].get("source_pdf", "?"), twin, d)
            continue

        # Within-batch, no-lag variant: this fetch pulled BOTH releases for date d
        # and one was published with NO LAG (pub <= d), so BB stamped it with the
        # wrong date — the keep-latest filter above would silently drop it. Re-home
        # that release to the day the market traded with no OMO recorded.
        _nolag_pubs = {_pub(x) for x in rows
                       if _pub(x) not in (latest_pub, datetime.date.min) and _pub(x) <= d}
        if len(_nolag_pubs) == 1:
            _p = _nolag_pubs.pop()
            target = _prior_gap_day(session, d)
            if target is not None:
                for x in (r for r in rows if _pub(r) == _p):
                    x2 = dict(x)
                    x2["transaction_date"] = target
                    x2["maturity_date"]    = target + datetime.timedelta(days=(x.get("tenor_days") or 0))
                    _insert(x2); saved += 1
                log.error("OMO mislabel guard (batch): no-lag release (pub %s) re-homed from "
                          "'as on %s' to %s.", _p, d, target)

        # Within-batch mislabel: if this one fetch pulled BOTH releases for date d
        # and the OLDER one carries a CB Repo the latest lacks, the keep-filter
        # above would silently drop it. That CB Repo is a different (Tuesday)
        # operation BB double-dated — re-home the whole older release to its
        # Tuesday instead of losing it. (Cross-run duplicates are caught below.)
        if not any(k["instrument"] == "CB_REPO" and k["accepted_bdt_crore"] > 0 for k in keep):
            older_cb_pub = max((_pub(x) for x in rows
                                if _pub(x) != latest_pub and x["instrument"] == "CB_REPO" and x["accepted_bdt_crore"] > 0),
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

        # ── Mislabel guard A: a release published with NO LAG is mis-dated ───
        # BB publishes a day's operations the NEXT working day, so a release whose
        # 'as on' date equals (or precedes) its own publication date has BB's date
        # wrong — it is an EARLIER operation. When a second, normally-lagged
        # release claims the same 'as on' date, keying on the date alone deletes
        # one of two REAL operations (Sep-2026: the 03-Sep op, published 06-Sep
        # and stamped 'as on 06 Sep', was dropped by the genuine 06-Sep release).
        # Re-home the no-lag release to the day the market traded but no OMO was
        # recorded. Instrument-agnostic and works on either fetch order.
        if existing and inc_pub is not None and stored_pub is not None and inc_pub != stored_pub:
            stored_nolag = stored_pub <= d
            inc_nolag    = inc_pub <= d
            if stored_nolag != inc_nolag:          # exactly one side is mis-dated
                target = _prior_gap_day(session, d)
                if target is not None:
                    if stored_nolag:   # move the stored mis-dated op, keep incoming on d
                        for e in existing:
                            e.transaction_date = target
                            e.maturity_date    = target + datetime.timedelta(days=e.tenor_days or 0)
                        session.flush()
                        for r in keep:
                            _insert(r); saved += 1
                    else:              # incoming is mis-dated → store it on the gap day
                        for r in keep:
                            r2 = dict(r)
                            r2["transaction_date"] = target
                            r2["maturity_date"]    = target + datetime.timedelta(days=(r.get("tenor_days") or 0))
                            _insert(r2); saved += 1
                    log.error("OMO mislabel guard: no-lag release re-homed from 'as on %s' to %s "
                              "— BB double-dated two operations (%s traded but had no OMO).",
                              d, target, target)
                    continue

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
            stored_has_cb = any(e.instrument == "CB_REPO" and (e.accepted_bdt_crore or 0) > 0 for e in existing)
            inc_has_cb    = any(r["instrument"] == "CB_REPO" and r["accepted_bdt_crore"] > 0 for r in keep)
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


def _register_source(session, snap: dict) -> dict:
    """Record what the source LOOKED LIKE, and say whether it moved.

    Separate from recording the data, deliberately: BB renaming a CSS class or
    inserting a column is not a data event, produces no error, and is invisible
    to every freshness check — and it is the failure most likely to hand the
    desk a plausible wrong number. Returns {} when nothing moved.
    """
    from fetchers import source_watch as SW
    try:
        verdict, reasons = SW.record(session, snap)
        session.commit()
    except Exception as exc:            # never let the watcher break the fetch
        session.rollback()
        log.warning("source fingerprint not recorded for %s: %s", snap.get("source"), exc)
        return {}
    if verdict == "ok":
        return {}
    return {"source_verdict": verdict, "source_reasons": reasons}


def run_holiday_fetch() -> dict:
    """Read BB's published holiday list and seed holiday_calendar from it.

    Nothing read this page until 15-Oct-2026. The calendar held 33 rows ending
    26-Aug-2026, the recent ones marked INFERRED_CLOSURE — "the whole money
    market went silent, so this was probably a holiday". That works backwards
    and not at all forwards, so the system did not know that 20 and 21 October
    2026 are Durga Puja: a Tuesday and a Wednesday, five days out, with every
    settlement date and working-day age across them computed wrong.

    Three rules, because a wrong holiday moves real settlement dates off real
    days and is worse than a missing one:

      * an INFERRED_CLOSURE on a date BB gazettes is UPGRADED, taking BB's own
        name — that is exactly what its placeholder text asked for;
      * a WORKING_DAY row is never touched. BB declares specific working
        Saturdays (23-May-2026), and a clash with its own holiday list is a
        contradiction to report, not to resolve by overwriting;
      * a date we hold that BB does not list is left alone and reported. BB's
        page covers the current year; silence there is not a denial.
    """
    from config import fiscal_year
    from fetchers.holidays import fetch_holidays
    init_db()
    session = get_session()
    try:
        out = fetch_holidays()
        rows = out["rows"]
        watch = _register_source(session, out["meta"])
        if not rows:
            msg = f"holidays: fetched nothing — {out.get('reason') or 'no reason reported'}"
            log.error(msg)
            return {"rows": 0, "errors": [msg], "empty_reason": out.get("reason"), **watch}

        existing = {h.calendar_date: h for h in session.query(HolidayCalendar).all()}
        added = upgraded = conflicts = unchanged = 0
        notes = []
        for r in rows:
            d = r["calendar_date"]
            cur = existing.get(d)
            if cur is None:
                session.add(HolidayCalendar(
                    calendar_date=d, holiday_name=r["holiday_name"],
                    holiday_type=r["holiday_type"], fiscal_year=fiscal_year(d),
                    source=r["source"][:200]))
                added += 1
                notes.append(f"+ {d} {r['holiday_name']}")
            elif cur.holiday_type == "WORKING_DAY":
                conflicts += 1
                notes.append(f"! {d} is stored as a DECLARED WORKING DAY but BB's holiday "
                             f"list says {r['holiday_name']} — left as it was")
            elif cur.holiday_type != r["holiday_type"]:
                was = cur.holiday_type or "untyped"
                cur.holiday_name = r["holiday_name"]
                cur.holiday_type = r["holiday_type"]
                cur.source = r["source"][:200]
                upgraded += 1
                notes.append(f"~ {d} {r['holiday_name']} (was {was})")
            else:
                unchanged += 1

        ours_only = [d for d in existing
                     if d.year in {r["calendar_date"].year for r in rows}
                     and d not in {r["calendar_date"] for r in rows}
                     and existing[d].holiday_type not in ("WORKING_DAY",)]
        session.commit()
        _load_holidays(session)
        log.info("Holidays: %d added, %d upgraded, %d unchanged, %d conflict(s)",
                 added, upgraded, unchanged, conflicts)
        return {"rows": len(rows), "added": added, "upgraded": upgraded,
                "unchanged": unchanged, "conflicts": conflicts,
                "ours_not_in_bbs_list": [str(d) for d in sorted(ours_only)],
                "notes": notes, "refused": out["refused"],
                "errors": [f"holiday_calendar: {n[2:]}" for n in notes if n.startswith("! ")],
                **watch}
    except Exception as exc:
        session.rollback()
        log.exception("Holiday fetch failed")
        return {"rows": 0, "errors": [str(exc)]}
    finally:
        session.close()


def store_press_rows(session, rows: list) -> tuple:
    """Upsert classified press releases, keyed on BB's own serial.

    Re-reading the listing refreshes `last_seen_utc` and nothing else, so a
    release BB has edited keeps its first sighting and one BB has removed stops
    being seen without being deleted. Returns (added, revised).
    """
    now = datetime.datetime.utcnow()
    existing = {r.serial: r for r in session.query(PressRelease).all()}
    added = updated = 0
    for r in rows:
        cur = existing.get(r["serial"])
        if cur is None:
            session.add(PressRelease(
                serial=r["serial"], published_date=r["published_date"],
                subject_date=r["subject_date"], title=(r["title"] or "")[:2000],
                url=r["url"], category=r["category"], isin=r["isin"],
                first_seen_utc=now, last_seen_utc=now))
            added += 1
        else:
            cur.last_seen_utc = now
            # BB does re-post a corrected release under the same serial.
            if cur.title != r["title"] or cur.category != r["category"]:
                cur.title, cur.category = (r["title"] or "")[:2000], r["category"]
                cur.subject_date, cur.isin = r["subject_date"], r["isin"]
                updated += 1
    return added, updated


def run_press_fetch() -> dict:
    """Read BB's press-release listing, classify it and keep it.

    This is the listing the OMO fetch reads for its PDFs, so the two share a
    parser and a markup change is one fix rather than two. Keeping the whole
    listing is what makes an unanswered question answerable: BB announced
    Bangladesh's first bond buyback on 05-Oct-2026 and its result on the 8th,
    and nothing in this system noticed either — the 1,716.14 crore that
    reached the market on the 12th simply never appeared in the ladder.

    Stores one row per release, keyed on BB's own serial (pr14322). Re-reading
    the listing refreshes `last_seen_utc` and nothing else: a release BB has
    edited keeps its first sighting, and one BB has removed stops being seen
    without being deleted.
    """
    from fetchers.press import fetch_press_releases
    init_db()
    session = get_session()
    try:
        out = fetch_press_releases()
        rows = out["rows"]
        watch = _register_source(session, out["meta"])
        if not rows:
            msg = f"press: fetched nothing — {out.get('reason') or 'no reason reported'}"
            log.error(msg)
            return {"rows": 0, "errors": [msg], "empty_reason": out.get("reason"), **watch}

        added, updated = store_press_rows(session, rows)
        session.commit()
        by_cat: dict = {}
        for r in rows:
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        log.info("Press releases: %d seen, %d new, %d revised — %s",
                 len(rows), added, updated, by_cat)
        return {"rows": len(rows), "added": added, "revised": updated,
                "by_category": by_cat, "skipped": out["skipped"], "errors": [], **watch}
    except Exception as exc:
        session.rollback()
        log.exception("Press fetch failed")
        return {"rows": 0, "errors": [str(exc)]}
    finally:
        session.close()


def run_bgiib_fetch(limit: int = 12) -> dict:
    """Parse BGIIB (Islamic window) results out of the releases already captured.

    Reads the PDFs of press releases that have no stored auction yet — the
    listing itself is recorded by the OMO step, so this adds no page fetch of
    its own, only the PDFs it has not read before. Thirteen releases had been
    sitting there unparsed, which is why the Islamic window appeared nowhere.
    """
    from fetchers.islamic import fetch_bgiib
    init_db()
    session = get_session()
    try:
        out = fetch_bgiib(session, limit=limit)
        rows = out["rows"]
        if not rows:
            msg = out.get("reason")
            if out["refused"]:
                msg = f"{msg}; refused: {out['refused']}"
            if msg:
                log.warning("BGIIB: %s", msg)
            return {"rows": 0, "releases": 0, "refused": out["refused"],
                    "errors": [f"bgiib: {msg}"] if out["refused"] else []}

        now = datetime.datetime.utcnow()
        added = updated = 0
        for r in rows:
            existing = session.query(BgiibAuction).filter_by(
                auction_date=r["auction_date"], tenor_label=r["tenor_label"]).one_or_none()
            if existing is None:
                session.add(BgiibAuction(ingested_utc=now, **r))
                added += 1
            else:
                for k, v in r.items():
                    setattr(existing, k, v)
                existing.ingested_utc = now
                updated += 1
        session.commit()
        log.info("BGIIB: %d release(s) read, %d row(s) added, %d updated",
                 out["releases"], added, updated)
        return {"rows": len(rows), "releases": out["releases"], "added": added,
                "updated": updated, "refused": out["refused"],
                "errors": [f"bgiib: refused {out['refused']}"] if out["refused"] else []}
    except Exception as exc:
        session.rollback()
        log.exception("BGIIB fetch failed")
        return {"rows": 0, "errors": [str(exc)]}
    finally:
        session.close()


def run_sukuk_fetch() -> dict:
    """Read BB's BGIS Sukuk pages: the outstanding table and the notice / prospectus lists.

    Documents are stored with notified_utc NULL, except on the very first run
    (an empty table), when every document is marked already told: what BB posted
    before this existed is history, not news, and an alert that opens by crying
    wolf is read once.
    """
    from fetchers.sukuk import fetch_sukuk
    init_db()
    session = get_session()
    try:
        out = fetch_sukuk()
        watch = _register_source(session, out["meta"]) if out.get("meta") else {}
        now = datetime.datetime.utcnow()
        added = updated = changed = 0
        for r in out["outstanding"]:
            row = session.query(SukukOutstanding).filter_by(isin=r["isin"]).one_or_none()
            if row is None:
                session.add(SukukOutstanding(first_seen_utc=now, last_seen_utc=now,
                                             source="BB sukukoutstanding", **r))
                added += 1
            else:
                # What moves cash: if any of it changed the ladder is stale until rebuilt.
                if any(getattr(row, k) != r[k] for k in
                       ("outstanding_crore", "maturity_date", "issue_date", "profit_rate_pct",
                        "profit_frequency")):
                    changed += 1
                for k, v in r.items():
                    setattr(row, k, v)
                row.last_seen_utc = now
                updated += 1
        first_ever = session.query(SukukDocument).count() == 0
        docs_added = 0
        known = {k for (k,) in session.query(SukukDocument.doc_key).all()}
        for d in out["documents"]:
            if d["doc_key"] in known:
                continue
            session.add(SukukDocument(
                doc_key=d["doc_key"], url=d["url"], kind=d["kind"], doc_date=d["doc_date"],
                ref=d.get("ref"), title=d["title"], first_seen_utc=now,
                notified_utc=now if first_ever else None))
            known.add(d["doc_key"])
            docs_added += 1
        session.commit()
        log.info("Sukuk: %d issue(s) (%d new), %d new document(s)%s", len(out["outstanding"]),
                 added, docs_added, " [baseline: marked already told]" if first_ever else "")
        errs = list(out["errors"])
        if out["refused"]:
            errs.append(f"sukuk: refused {out['refused']}")
        return {"rows": len(out["outstanding"]), "added": added, "updated": updated,
                "changed": changed, "documents_added": docs_added, "refused": out["refused"],
                "errors": errs, **watch}
    except Exception as exc:
        session.rollback()
        log.exception("Sukuk fetch failed")
        return {"rows": 0, "errors": [f"sukuk: {exc}"]}
    finally:
        session.close()


def run_omo_fetch(days_back: int = 28, max_files: int = 20) -> dict:
    """Fetch last N OMO press release PDFs and store transactions to DB.

    Tries the no-browser path first: BB's listing and its PDFs both come back
    over curl_cffi, which is faster and has fewer moving parts than driving
    Chrome. Chrome remains the fallback.

    A fetch that produces NOTHING now returns a reason. It used to return an
    empty list after logging an error, so the caller recorded "OMO OK |
    new_rows=0" and eleven days of missing data looked exactly like a quiet
    market.
    """
    from fetchers.omo import fetch_omo_data, fetch_omo_no_browser
    init_db()
    session = get_session()
    try:
        # Load the calendar from the DATABASE before parsing anything. A
        # tranche's maturity is derived as transaction + tenor rolled to the
        # next working day AT PARSE TIME, so a process that never loaded the
        # real holidays writes dates onto closures — which is how 15,464.92
        # crore came to be shown maturing across Durga Puja twice: once from an
        # old calendar, and again when CI re-derived it. Lazy auto-load covers
        # most paths; this one is too consequential to leave to it.
        _load_holidays(session)
        meta, reason = {}, None
        try:
            res = fetch_omo_no_browser(days_back=days_back, max_files=max_files)
            txns = res["rows"]
            reason = res.get("reason")
            meta = {k: v for k, v in res.items() if k not in ("rows", "pdf_failures")}
        except Exception as exc:
            log.warning("OMO no-browser fetch failed (%s) — falling back to Chrome", exc)
            txns, reason = fetch_omo_data(days_back=days_back, max_files=max_files), None
            meta = {"selector": "chrome-fallback"}
            if not txns:
                reason = f"no-browser path raised {type(exc).__name__}; Chrome returned nothing"

        # Which selector matched, how many anchors it found, how many rows the
        # listing had. When BB renamed `a.pdf-file` to `a.pdf-list` all three
        # moved at once, eleven days before anyone noticed the data.
        from fetchers import source_watch as SW
        from fetchers.omo import BB_PRESS_URL as _OMO_URL
        watch = _register_source(session, SW.fingerprint(
            "omo_press", _OMO_URL, selector=meta.get("selector") or "",
            rows_seen=meta.get("rows_seen") or 0, rows_parsed=len(txns),
            skips={"not an OMO release": meta.get("skipped_not_omo") or 0,
                   "older than the window": meta.get("skipped_too_old") or 0},
            # Anchors are what tell the two apart: links were found and none
            # parsed (structural), versus the page never arrived (transport).
            error=((reason or "") if (not txns and meta.get("anchors_found")) else ""),
            transport_error=("" if meta.get("anchors_found")
                             else (reason or "the listing was not read"))
                            if not txns else ""))

        # The listing this fetch just downloaded, kept whole. Daily, because
        # that is how often BB announces things — a weekly watcher would have
        # taken a week to notice the first buyback, which is about how long it
        # took to notice by hand.
        press_added = press_revised = 0
        try:
            from fetchers.press import classify_links
            if meta.get("all_links"):
                pr = classify_links(meta["all_links"])
                press_added, press_revised = store_press_rows(session, pr["rows"])
                session.commit()
                if press_added:
                    log.info("  press listing: %d new release(s) recorded", press_added)
        except Exception as exc:
            session.rollback()
            log.warning("  press listing not stored: %s", exc)
        meta.pop("all_links", None)

        if not txns:
            # Empty is a RESULT, not a success. Say why, and say it where the
            # pipeline run records it.
            msg = f"omo: fetched nothing — {reason or 'no reason reported'}"
            log.error(msg)
            return {"rows": 0, "pdfs": 0, "errors": [msg], "empty_reason": reason,
                    **watch, **meta}

        saved, superseded = _store_omo_txns(session, txns, datetime.datetime.utcnow())
        session.commit()
        log.info("OMO fetch: %d rows stored, %d date(s) superseded by corrections, from %d txns",
                 saved, superseded, len(txns))
        recon = sorted({t["recon_mismatch"] for t in txns if t.get("recon_mismatch")})
        return {"rows": saved, "txns": len(txns), "pdfs": meta.get("pdfs", max_files),
                "errors": recon, **watch, **meta}
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
        _load_holidays(session)   # auction_date = previous working day of the printed issue date
        rows = fetch_primary_yields_history(months_back=months_back)
        for r in rows:
            upsert_primary_yield(session, r)
        session.commit()
        # One fingerprint per page read. The newest page is the one that matters
        # for "has BB moved today"; the rest are registered so a shape BB uses
        # only on older months is known-good rather than a surprise.
        from fetchers.treasury import LAST_STRUCTURE
        watch, errs = {}, []
        for snap in LAST_STRUCTURE:
            w = _register_source(session, snap)
            if w:
                watch = w
                if w.get("source_verdict") == "degraded":
                    errs.append("treasury page structure: " + "; ".join(w["source_reasons"]))
        log.info("Primary yield history: stored %d rows (%d months)", len(rows), months_back)
        return {"rows": len(rows), "months": months_back, "errors": errs, **watch}
    except Exception as exc:
        session.rollback()
        log.exception("Primary yield history failed")
        return {"rows": 0, "months": months_back, "errors": [str(exc)]}
    finally:
        session.close()


# ══════════════════════════════════════════════════════════════════════════════

def rebuild_daily_flows(session, date_from: datetime.date = None,
                        date_to: datetime.date = None) -> int:
    """Rebuild daily_net_flow from the events currently in the database.

    Extracted from run_pipeline because it is needed in three places and was
    being re-written by hand each time. The ladder is a MATERIALISED aggregate:
    the moment the self-heal moves a coupon off Durga Puja, or a settlement
    date rolls, every row it feeds is stale until this runs. It touches no
    network — it reads events and writes the aggregate — so there is no excuse
    for the hourly refresh to skip it after a repair.

    Returns the number of daily rows written.
    """
    from sqlalchemy import delete
    date_from = date_from or datetime.date.today() - datetime.timedelta(days=120)
    date_to = date_to or datetime.date.today() + datetime.timedelta(days=365)

    # Re-query ALL events from the DB for the range.
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
        # The cash paid, which is what drains liquidity on settlement day.
        "sale_value_bdt_mill":         a.sale_value_bdt_mill,
        "outflow_status":              a.outflow_status,
        "security_type":               a.security_type,
        "data_quality":                a.data_quality,
    } for a in db_auctions]

    log.info(
        "DB query for range %s→%s: %d coupons, %d maturities, %d auctions",
        date_from, date_to,
        len(coupon_dicts), len(maturity_dicts), len(auction_dicts)
    )

    # Aggregate.
    # Buybacks: early principal repayments BB settles outside the maturity
    # schedule. One row per event, read straight from its own table so a
    # pipeline re-run can never drop it.
    buyback_dicts = [{
        "isin":                 b.isin,
        "settlement_date":      b.settlement_date,
        "accepted_face_crore":  b.accepted_face_crore,
        "cash_paid_crore":      b.cash_paid_crore,
    } for b in session.query(BuybackEvent).filter(
        BuybackEvent.settlement_date >= date_from,
        BuybackEvent.settlement_date <= date_to).all()]
    if buyback_dicts:
        log.info("  %d buyback event(s) in range", len(buyback_dicts))

    # BGIS sukuk: principal and half-yearly profit from BB's outstanding table.
    from engines.aggregation import sukuk_cash_flows
    sukuk_flows = sukuk_cash_flows([{
        "isin": s.isin, "issue_date": s.issue_date, "maturity_date": s.maturity_date,
        "outstanding_crore": s.outstanding_crore, "profit_rate_pct": s.profit_rate_pct,
        "profit_frequency": s.profit_frequency,
    } for s in session.query(SukukOutstanding).all()], date_from, date_to)
    if sukuk_flows:
        log.info("  %d sukuk cash flow(s) in range", len(sukuk_flows))

    daily_rows = build_daily_flows(
        coupon_dicts, maturity_dicts, auction_dicts,
        date_from, date_to, buyback_events=buyback_dicts, sukuk_flows=sukuk_flows,
    )

    # Persist daily_net_flow, replacing the range.
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
    return len(daily_rows)


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

        # ── 6b. Results → calendar: actual accepted amounts and issue dates ──
        confirm_auctions_from_results(session)
        session.commit()

        # Primary yield history is fetched separately via the sidebar button
        # (fetch_primary_yields_history) — not run here to keep pipeline fast.

        # ── 7. Set date range ─────────────────────────────────────────────────
        if date_from is None:
            date_from = datetime.date.today() - datetime.timedelta(days=60)
        if date_to is None:
            date_to   = datetime.date.today() + datetime.timedelta(days=365)

        # ── 8-10. Rebuild the ladder from the events now in the DB ───────────
        summary["daily_rows"] = rebuild_daily_flows(session, date_from, date_to)

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
