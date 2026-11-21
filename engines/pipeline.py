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
import json
import logging
import re
from typing import Callable, List, Optional

from sqlalchemy.exc import DataError, IntegrityError

import calendar_utils
from config import GSOM_TBOND_URL, GSOM_FRTB_URL, GSOM_TBILL_URL
from db import (
    init_db, get_session,
    Security, CouponEvent, MaturityEvent, BuybackEvent, PressRelease,
    BgiibAuction, SukukOutstanding, SukukDocument, SukukAuction,
    AuctionEvent, HolidayCalendar, DailyNetFlow, MtmSnapshot,
    PrimaryYieldSnapshot, OMOTransaction,
    QuarantinedRow, CONSTRAINT_FIELDS, DataChange,
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


def propagate_calendar_change(session) -> dict:
    """Bring everything derived from the holiday calendar into line after the calendar changed.

    A new closure moves dates that were computed under the old calendar: OMO tranche maturities,
    coupon and maturity payment dates, planned auction settlements. The self-heal re-rolls them
    (it runs at the start of every refresh, but a refresh that learns a closure mid-run must not wait
    for the next one) and the ladder, a materialised aggregate, is rebuilt from the corrected events.
    Idempotent and offline: a second call changes nothing."""
    from engines.guardian import record_repairs
    from engines.repair import self_heal
    _load_holidays(session)                    # the process-wide cache would otherwise keep the old calendar
    healed = self_heal(session)
    session.commit()
    rebuilt = rebuild_daily_flows(session)
    session.commit()
    try:
        record_repairs(session, healed)
        session.commit()
    except Exception as exc:                   # the audit trail must not undo the repair
        session.rollback()
        log.warning("calendar change: repairs not logged: %s", exc)
    return {"repaired": healed.get("repaired") or {}, "ladder_rows_rebuilt": rebuilt}


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


_CHECK_NAME_PATTERNS = (
    re.compile(r'violates check constraint "([^"]+)"'),          # psycopg2 / Postgres
    re.compile(r'CHECK constraint failed:\s*([A-Za-z0-9_]+)'),   # sqlite3
)


def constraint_name(exc) -> Optional[str]:
    """The name of the CHECK constraint a database error says was violated, or None.

    Pure text work over the driver's message (Postgres: 'violates check constraint "ck_repo_rate_band"', SQLite:
    'CHECK constraint failed: ck_repo_rate_band'), so a new driver wording yields None and the row is quarantined
    rather than blanked on a guess."""
    text = str(getattr(exc, "orig", None) or exc)
    for pat in _CHECK_NAME_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def _json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    return str(o)


def _jsonable(row: dict) -> dict:
    """The row as plain JSON values (dates as ISO strings), so it can be stored and compared."""
    return json.loads(json.dumps(row, default=_json_default))


def _write_isolated(session, model, row: dict, write: Callable[[dict], str]) -> dict:
    """Write ONE row inside its own savepoint; on a violated CHECK constraint retry once with exactly its fields blank.

    `write(row)` performs the insert or update and returns a label for what it did. The outcome is one of
      {"outcome": "stored", "kind": label}
      {"outcome": "blanked", "kind": label, "constraint": name, "fields": [...], "original": {...}, "row": {...}}
      {"outcome": "quarantined", "constraint": name or None, "error": text, "raw": {...}}
    The caller decides what to do with a quarantined row (store_rows_isolated persists it at once; replace_daily_rows
    persists it after the day's own savepoint is settled). Only IntegrityError is handled here: a lost connection or a
    type error is not a row problem and must keep propagating."""
    try:
        with session.begin_nested():
            kind = write(row)
            session.flush()
        return {"outcome": "stored", "kind": kind}
    except IntegrityError as exc:
        first = exc
    name = constraint_name(first)
    # Blank ONLY what the constraint names, and only what the row actually carries. A constraint we do not know, or one
    # whose fields the row does not even hold, proves nothing about which number is wrong: quarantine, do not guess.
    fields = [f for f in CONSTRAINT_FIELDS.get(name, ()) if f in row] if name else []
    err = str(getattr(first, "orig", None) or first)[:500]
    if fields:
        blanked = dict(row)
        for f in fields:
            blanked[f] = None
        try:
            with session.begin_nested():
                kind = write(blanked)
                session.flush()
            return {"outcome": "blanked", "kind": kind, "constraint": name, "fields": fields,
                    "original": _jsonable({f: row[f] for f in fields}), "row": _jsonable(row)}
        except IntegrityError as exc2:
            name = constraint_name(exc2) or name
            err = str(getattr(exc2, "orig", None) or exc2)[:500]
    return {"outcome": "quarantined", "constraint": name, "error": err, "raw": _jsonable(row)}


def _quarantine(session, model, step: str, info: dict, now: datetime.datetime) -> None:
    """Persist a quarantined row once: the same unchanged row, re-read every hour, must not pile up as 32 copies."""
    table = model.__tablename__
    volatile = ("ingested_utc",)
    raw = {k: v for k, v in info["raw"].items() if k not in volatile}
    for q in session.query(QuarantinedRow).filter_by(step=step, table_name=table).all():
        try:
            held = {k: v for k, v in json.loads(q.raw_json).items() if k not in volatile}
        except ValueError:
            continue
        if held == raw:
            return
    session.add(QuarantinedRow(step=step, table_name=table, raw_json=json.dumps(info["raw"], sort_keys=True),
                               constraint=info["constraint"], error=info["error"], first_seen=now))
    session.flush()


def store_rows_isolated(session, model, rows: list, step: str, now: Optional[datetime.datetime] = None,
                        write: Optional[Callable[[dict], str]] = None) -> dict:
    """Store every row on its own, so one row BB prints against itself costs that row's bad fields and nothing else.

    On 11-Nov-2026 BB printed a repo row whose weighted average lay outside its own range; the database CHECK refused it
    and one flush for the whole month threw away three good days with it. Here each row is written in its own savepoint.
    A row that breaks a CHECK constraint is retried ONCE with exactly the fields that constraint names set to NULL
    (CONSTRAINT_FIELDS): blank, never a guessed value, and every other field (num_deals, amount) is kept. If that also
    fails, or the constraint is unknown, the raw row goes to quarantined_rows. A row is never dropped silently.

    `write(row) -> label` overrides the plain insert (a caller with its own upsert); the labels are counted in "kinds".
    Returns {"stored": n, "blanked": [...], "quarantined": [...], "kinds": {...}}; "stored" includes the blanked rows
    (they are stored), so stored + len(quarantined) == len(rows).
    """
    now = now or datetime.datetime.utcnow()

    def _insert(r: dict) -> str:
        session.add(model(**r))
        return "inserted"

    out = {"stored": 0, "blanked": [], "quarantined": [], "kinds": {}}
    for r in rows:
        res = _write_isolated(session, model, r, write or _insert)
        if res["outcome"] == "quarantined":
            _quarantine(session, model, step, res, now)
            out["quarantined"].append({k: res[k] for k in ("constraint", "error", "raw")})
            log.error("%s: row quarantined (%s): %s", step, res["constraint"], res["error"][:200])
            continue
        out["stored"] += 1
        out["kinds"][res["kind"]] = out["kinds"].get(res["kind"], 0) + 1
        if res["outcome"] == "blanked":
            out["blanked"].append({k: res[k] for k in ("constraint", "fields", "original", "row")})
            log.warning("%s: row stored with %s blank (breaks %s)", step, ", ".join(res["fields"]), res["constraint"])
    return out


class _DayRefused(Exception):
    """A day holds a row the database refuses even with its offending fields blank."""


def replace_daily_rows(session, model, rows: list, key_fields: tuple,
                       date_field: str = "trade_date", step: Optional[str] = None) -> dict:
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
    step = step or model.__tablename__
    now = datetime.datetime.utcnow()
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r[date_field], []).append(r)
    stats = {"updated": 0, "inserted": 0, "deleted": 0, "unchanged": 0}
    for d, page_rows in by_date.items():
        if not page_rows:
            continue
        # ONE BAD ROW MUST NOT LOSE ITS DAY, AND ONE BAD DAY MUST NOT LOSE THE OTHERS. A single BB row that broke a database
        # rule (a repo row whose weighted average lay outside its own range) aborted the one flush for the whole month, so
        # 10, 12 and 15 Nov never reached the table for four days. Each date is written inside its own savepoint, and each
        # row inside that: a row that breaks a CHECK constraint is stored with exactly that constraint's fields blank
        # (stats["blanked"]), costing the day nothing else. A day that still holds a row the database refuses is skipped,
        # named in stats["rejected"] and reported by the caller, its raw rows go to quarantined_rows (stats["quarantined"]),
        # and every other day is kept.
        day = {"updated": 0, "inserted": 0, "deleted": 0, "unchanged": 0}
        blanked: list = []
        quarantined: list = []
        try:
            with session.begin_nested():
                _replace_one_day(session, model, d, page_rows, key_fields, date_field, day, blanked, quarantined)
                session.flush()
                if quarantined:
                    raise _DayRefused(f"{len(quarantined)} row(s) refused even with their offending fields blank")
        except Exception as exc:
            session.expire_all()
            log.error("replace_daily_rows %s %s rejected by the database: %s", model.__name__, d, str(exc)[:200])
            stats.setdefault("rejected", []).append(str(d))
            blanked = []          # that day was rolled back: nothing on it was stored, blanked or otherwise
        else:
            for k, v in day.items():
                stats[k] += v
        for info in quarantined:
            _quarantine(session, model, step, info, now)
        if blanked:
            stats.setdefault("blanked", []).extend(blanked)
        if quarantined:
            stats.setdefault("quarantined", []).extend(
                {k: info[k] for k in ("constraint", "error", "raw")} for info in quarantined)
    session.flush()
    return stats


def _replace_one_day(session, model, d, page_rows: list, key_fields: tuple, date_field: str, stats: dict,
                     blanked: list, quarantined: list) -> None:
    """One date's rows: update what changed, insert what is new, delete what the page no longer has.

    Each row goes through _write_isolated, so a row that breaks a CHECK constraint is retried with the constraint's
    own fields blank (appended to `blanked`) or, failing that, appended to `quarantined` for the caller to settle."""
    existing = session.query(model).filter(getattr(model, date_field) == d).all()
    ex_by_key = {tuple(getattr(e, k) for k in key_fields): e for e in existing}
    seen = set()
    for r in page_rows:
        key = tuple(r.get(k) for k in key_fields)
        seen.add(key)
        e = ex_by_key.get(key)

        def write(row, e=e) -> str:
            if e is None:
                session.add(model(**row))
                return "inserted"
            changed = False
            for f, v in row.items():
                if f in ("ingested_utc", "id") or not hasattr(e, f):
                    continue
                if getattr(e, f) != v:
                    setattr(e, f, v); changed = True
            if not changed:
                return "unchanged"
            if hasattr(e, "ingested_utc") and "ingested_utc" in row:
                e.ingested_utc = row["ingested_utc"]
            return "updated"

        res = _write_isolated(session, model, r, write)
        if res["outcome"] == "quarantined":
            quarantined.append(res)
            continue
        stats[res["kind"]] += 1
        if res["outcome"] == "blanked":
            blanked.append({k: res[k] for k in ("constraint", "fields", "original", "row")})
    for key, e in ex_by_key.items():
        if key not in seen:
            session.delete(e); stats["deleted"] += 1


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
    # The same auction under two labels. BB files a re-issue onto its tenor grid, and a 2.6-year bond has been read as 2Y and later as
    # 5Y. The calendar row it was confirmed under (2Y) then matched no result, the result looked ad hoc, and an uncalendared twin was
    # made: 3-Feb-2026 booked 25,000 mn twice (ids 136 and 346, identical cut-off and sale value, ONE yields row). When an uncalendared
    # row has an UNCLAIMED calendar row of the same security type, auction date, settlement date and size (exactly one), that calendar
    # row IS the auction: it takes BB's current label and the twin goes. Recorded in data_changes; a clean table never triggers it.
    session.flush()
    twins = 0
    for u in [a for a in session.query(AuctionEvent).all() if a.source == _UNCALENDARED and id(a) in claimed]:
        same = [c for c in cal if c.source != _UNCALENDARED and id(c) not in claimed and c.security_type == u.security_type
                and c.auction_date == u.auction_date and c.settlement_date == u.settlement_date
                and c.offered_amount_bdt_mill is not None and u.offered_amount_bdt_mill is not None
                and abs(float(c.offered_amount_bdt_mill) - float(u.offered_amount_bdt_mill)) < 0.5]
        if len(same) != 1:
            continue
        c = same[0]
        session.add(DataChange(changed_utc=datetime.datetime.utcnow(), change_class="auction_twin", detail=(
            f"auction_events {u.id} ({u.tenor_label}, uncalendared) was the calendar auction {c.id} ({c.tenor_label}) under another label: "
            f"{u.security_type} {u.auction_date} settling {u.settlement_date}, {float(u.offered_amount_bdt_mill):g} mn, same result. "
            f"Kept {c.id} with BB's label {u.tenor_label}; removed {u.id}")))
        for field in ("accepted_amount_bdt_crore", "accepted_amount_bdt_mill", "sale_value_bdt_crore", "sale_value_bdt_mill",
                      "weighted_avg_yield_pct", "outflow_status"):
            setattr(c, field, getattr(u, field))
        c.tenor_label = u.tenor_label
        claimed.add(id(c))
        session.delete(u)
        twins += 1

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
            "orphans_removed": len(orphans), "twins_merged": twins}


def _label_nearer_remaining(existing, row: dict) -> dict:
    """Of the stored row's label and the incoming one, the label whose tenor-grid point is nearest the bond's remaining life.

    Order-independent. With no readable remaining life, or equal distance, the stored label stays (a stable answer beats a flip)."""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", str(row.get("reissue_remaining") or existing.reissue_remaining or ""))
    stored = {"tenor_label": existing.tenor_label, "tenor_years": existing.tenor_years}
    incoming = {"tenor_label": row["tenor_label"], "tenor_years": row.get("tenor_years", existing.tenor_years)}

    def grid_years(c):          # the label IS the grid point ('2Y' -> 2); fall back to the stored years for labels without one
        g = re.match(r"(\d+)Y", str(c["tenor_label"]))
        return float(g.group(1)) if g else (float(c["tenor_years"]) if c["tenor_years"] is not None else None)

    a, b = grid_years(stored), grid_years(incoming)
    if not m or a is None or b is None:
        return stored
    left = float(m.group(1))
    return incoming if abs(b - left) < abs(a - left) else stored


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
                # BB's page can list one auction under two labels (3-Feb-2026: 2Y and 5Y, 2.60 years remaining). Whichever was written
                # LAST used to win, so the label flipped on every re-read, and each flip made the auction ledger book it twice. Pick by
                # the bond's own remaining life - the nearer tenor-grid point - so the answer no longer depends on the order read.
                keep = _label_nearer_remaining(by_isin, row)
                log.warning("yield row for %s on %s was stored as %s and BB also reads %s — keeping %s "
                            "(nearer to the %s remaining), one row, never two",
                            row["isin"], row["auction_date"], by_isin.tenor_label, row["tenor_label"], keep["tenor_label"],
                            row.get("reissue_remaining") or by_isin.reissue_remaining)
                row = {**row, "tenor_label": keep["tenor_label"], "tenor_years": keep["tenor_years"]}
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

SECONDARY_FILL_DAYS = 10      # how far back a missed working day is looked for and re-read from BB's picker


def backfill_secondary_gaps(session, today: datetime.date = None, fetch_page=None) -> dict:
    """Re-read from BB any recent WORKING day the secondary market (GSOM) has no snapshot for.

    BB's date picker still serves past days, so a run that was blocked or never ran loses nothing. Only
    days after the first snapshot we hold are considered (this fills holes, it does not extend history), and
    a row is stored only when the page itself labels it with that day and its security is in the master:
    BB's label decides the date, never the date we asked for. Returns {"filled": {date: rows}, "errors": []}."""
    import calendar_utils
    from sqlalchemy import func
    from fetchers import gsom as G
    from parsers.securities import parse_tbond_or_frtb, parse_tbill, validate_rows
    today = today or datetime.date.today()
    fetch_page = fetch_page or G.fetch_gsom_html_for
    out = {"filled": {}, "errors": []}
    first = session.query(func.min(MtmSnapshot.settlement_date)).scalar()
    if first is None:
        return out
    held = {d for (d,) in session.query(MtmSnapshot.settlement_date).filter(
        MtmSnapshot.settlement_date >= today - datetime.timedelta(days=SECONDARY_FILL_DAYS)).distinct()}
    known = {i for (i,) in session.query(Security.isin).all()}
    for back in range(1, SECONDARY_FILL_DAYS + 1):
        d = today - datetime.timedelta(days=back)
        if d <= first or d in held or not calendar_utils.is_working_day(d):
            continue
        n = 0
        try:
            for stype, is_bill in (("T_BOND", False), ("FRTB", False), ("T_BILL", True)):
                html = fetch_page(stype, d)
                if not html:
                    continue
                url = G._HTML_URLS[stype]
                rows = validate_rows(parse_tbill(html, url) if is_bill else parse_tbond_or_frtb(html, url, stype))
                for r in rows:
                    if r.get("settlement_date") == d and r.get("isin") in known:
                        _upsert_mtm_snapshot(session, r)
                        n += 1
            session.commit()
        except Exception as exc:
            session.rollback()
            out["errors"].append(f"secondary {d}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if n:
            out["filled"][str(d)] = n
            log.warning("Secondary market: %s was missing; %d snapshot(s) re-read from BB's date picker", d, n)
    return out


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


def _omo_pub(x) -> datetime.date:
    return x.get("source_pub_date") or datetime.date.min


def _omo_sig(rs):
    return tuple(sorted((x[0], int(x[1] or 0), round(float(x[2] or 0), 2), round(float(x[3] or 0), 2))
                        for x in rs))


def _store_omo_txns(session, txns: list, now: datetime.datetime, stats: Optional[dict] = None) -> tuple:
    """
    Store OMO rows with correction-aware supersession, returning (saved,
    superseded) - still exactly two values; refused/blanked rows are reported
    through the optional `stats` dict ("rejected", "blanked", "quarantined"). For each operation ('as on') date, only the LATEST-published
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

    # A tranche whose printed tenor BB's own later maturity print contradicts (data/seeds/known_absences.yaml
    # `tenor_corrections`): fix the stored row FIRST, then the incoming one, so re-reading the PDF never stores
    # the printed tenor beside the corrected row. A broken seed must not stop BB's data being stored.
    try:
        from engines.absences import apply_tenor_correction, known_tenor_corrections, sync_tenor_corrections
        sync_tenor_corrections(session)
        _tc = known_tenor_corrections(session=session)
        for _r in txns:
            apply_tenor_correction(_r, _tc)
    except Exception as exc:
        log.warning("OMO tenor corrections not applied: %s", exc)

    def _write(r: dict) -> str:
        # Builds the model from the row it is GIVEN: _write_isolated retries with the offending field blanked, and
        # that blank must be what reaches the database.
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
            offered_bdt_crore  = r.get("offered_bdt_crore"),
            net_bdt_crore      = r.get("net_bdt_crore"),
            printed_totals     = r.get("printed_totals"),
            direction          = r["direction"],
            source_pdf         = r.get("source_pdf"),
            source_pub_date    = r.get("source_pub_date"),
            source_serial      = r.get("source_serial"),
            ingested_utc       = now,
        ))
        return "inserted"

    by_date: dict = defaultdict(list)
    for r in txns:
        by_date[r["transaction_date"]].append(r)

    # Row-set signatures of every stored operation, for the duplicate-content
    # guard in _store_omo_day. (instrument, tenor, accepted, maturity) per row — an entire
    # table copied verbatim onto another date matches exactly.
    _sig = _omo_sig
    stored_sigs: dict = {}
    for r in session.execute(text("SELECT transaction_date, instrument, tenor_days, accepted_bdt_crore, "
                                  "COALESCE(maturity_bdt_crore, 0) FROM omo_transactions")).fetchall():
        dd = datetime.date.fromisoformat(r[0][:10]) if isinstance(r[0], str) else r[0]
        stored_sigs.setdefault(dd, []).append(r[1:])
    stored_sigs = {dd: _sig(rs) for dd, rs in stored_sigs.items()}

    # ONE REFUSED ROW MUST NOT LOSE THE OTHER DAYS. The whole batch used to be one flush: a single row the database
    # refused (a CHECK, or a duplicate line inside one release) threw away every day around it, and the next hourly run
    # hit the same row. Each operation date is now its own savepoint (the supersession delete-then-insert and the
    # re-homing guards, which move rows onto OTHER dates, are inside it, so a refused correction cannot half-replace a
    # stored release), and each row inside that goes through _write_isolated: a CHECK-violating row is stored with exactly
    # that constraint's fields blank (stats["blanked"]); a row still refused refuses its day (stats["rejected"]), its raw
    # row goes to quarantined_rows (stats["quarantined"]) and every other day is kept.
    # Only IntegrityError / DataError / _DayRefused mean "this day's data is bad". An OperationalError (a dead
    # connection, a timeout) is NOT a bad day: it propagates, so the caller fails loudly instead of marking every
    # remaining day as rejected.
    if stats is None:
        stats = {}
    saved = superseded = 0
    for d, rows in by_date.items():
        blanked: list = []
        quarantined: list = []

        def insert(r: dict, blanked=blanked, quarantined=quarantined) -> int:
            """Write one row; 1 if it is stored (blanked counts: it IS stored), 0 if it was quarantined."""
            res = _write_isolated(session, OMOTransaction, r, _write)
            if res["outcome"] == "quarantined":
                quarantined.append(res)
                return 0
            if res["outcome"] == "blanked":
                blanked.append({k: res[k] for k in ("constraint", "fields", "original", "row")})
            return 1

        try:
            with session.begin_nested():
                day_saved, day_superseded = _store_omo_day(session, d, rows, stored_sigs, insert)
                session.flush()
                if quarantined:
                    raise _DayRefused(f"{len(quarantined)} row(s) refused even with their offending fields blank")
        except (IntegrityError, DataError, _DayRefused) as exc:
            session.expire_all()
            log.error("OMO %s rejected by the database, the other days are kept: %s", d, str(exc)[:200])
            stats.setdefault("rejected", []).append(str(d))
            blanked = []          # that day was rolled back: nothing on it was stored, blanked or otherwise
        else:
            saved += day_saved
            superseded += day_superseded
        # Persist quarantined rows only AFTER the day's savepoint has settled (they live in another table).
        for info in quarantined:
            _quarantine(session, OMOTransaction, "omo", info, now)
        if blanked:
            stats.setdefault("blanked", []).extend(blanked)
        if quarantined:
            stats.setdefault("quarantined", []).extend(
                {k: info[k] for k in ("constraint", "error", "raw")} for info in quarantined)
    return saved, superseded


def omo_store_errors(stats: dict) -> list:
    """The `omo:` error lines for what _store_omo_txns refused or blanked, [] when it stored everything cleanly.

    Always ERRORS, never warnings, and on EVERY run: a blanked maturity_date drops that tranche from every outstanding
    query, and a refused day is a day of OMO missing. The wording 'rejected by a database rule' is what
    engines.failures.classify maps to DB_CONSTRAINT (same text as weekly_fetch.rejected_error)."""
    out = []
    if stats.get("rejected"):
        out.append(f"omo: BB rows for {', '.join(stats['rejected'])} were rejected by a database rule "
                   f"(the other days were stored)")
    if stats.get("quarantined"):
        q = stats["quarantined"]
        out.append(f"omo: {len(q)} OMO row(s) rejected by a database rule and quarantined: "
                   + "; ".join(f"{x['raw'].get('instrument')} {x['raw'].get('transaction_date')} "
                               f"({x['constraint'] or x['error'][:80]})" for x in q[:5]))
    if stats.get("blanked"):
        b = stats["blanked"]
        fields = sorted({f for x in b for f in x["fields"]})
        out.append(f"omo: {len(b)} OMO row(s) stored with {', '.join(fields)} blank because BB's figures were rejected by a "
                   f"database rule ({', '.join(sorted({x['constraint'] for x in b}))}); a tranche with no maturity date "
                   f"is missing from every outstanding figure until it is corrected")
    return out


def _store_omo_day(session, d, rows: list, stored_sigs: dict, insert) -> tuple:
    """ONE operation date's rows, returning (saved, superseded). The caller wraps this in a savepoint.

    `insert(row) -> 1|0` writes one row in isolation (1 when stored). The body is the former per-date loop of
    _store_omo_txns, unchanged apart from that."""
    saved = superseded = 0
    latest_pub = max((_omo_pub(x) for x in rows), default=datetime.date.min)
    keep = [x for x in rows if _omo_pub(x) == latest_pub]   # drop older release if both in batch
    if not keep:
        return 0, 0

    # ── Duplicate-content guard: a release whose WHOLE table equals another
    # date's operation is a re-published stale file, not a new operation.
    # BB's "as on 04 May 2026" (pr14062) was the 05-Apr table verbatim; it
    # put a phantom 2,587 cr IBLF tranche into outstanding for 28 days. Never
    # store it (and never let a re-fetch overwrite the reconstruction).
    inc_sig = _omo_sig([(x["instrument"], x.get("tenor_days"), x["accepted_bdt_crore"],
                     x.get("maturity_bdt_crore")) for x in keep])
    twin = next((dd for dd, sg in stored_sigs.items() if dd != d and sg == inc_sig and len(sg) >= 3), None)
    if twin is not None:
        log.error("OMO duplicate-content guard: release 'as on %s' (%s) is a verbatim copy of the "
                  "%s operation — BB re-published a stale table. NOT stored; the %s operation "
                  "must be reconstructed from BB's printed maturities.",
                  d, keep[0].get("source_pdf", "?"), twin, d)
        return saved, superseded

    # Within-batch, no-lag variant: this fetch pulled BOTH releases for date d
    # and one was published with NO LAG (pub <= d), so BB stamped it with the
    # wrong date — the keep-latest filter above would silently drop it. Re-home
    # that release to the day the market traded with no OMO recorded.
    _nolag_pubs = {_omo_pub(x) for x in rows
                   if _omo_pub(x) not in (latest_pub, datetime.date.min) and _omo_pub(x) <= d}
    if len(_nolag_pubs) == 1:
        _p = _nolag_pubs.pop()
        target = _prior_gap_day(session, d)
        if target is not None:
            for x in (r for r in rows if _omo_pub(r) == _p):
                x2 = dict(x)
                x2["transaction_date"] = target
                x2["maturity_date"]    = target + datetime.timedelta(days=(x.get("tenor_days") or 0))
                saved += insert(x2)
            log.error("OMO mislabel guard (batch): no-lag release (pub %s) re-homed from "
                      "'as on %s' to %s.", _p, d, target)

    # Within-batch mislabel: if this one fetch pulled BOTH releases for date d
    # and the OLDER one carries a CB Repo the latest lacks, the keep-filter
    # above would silently drop it. That CB Repo is a different (Tuesday)
    # operation BB double-dated — re-home the whole older release to its
    # Tuesday instead of losing it. (Cross-run duplicates are caught below.)
    if not any(k["instrument"] == "CB_REPO" and k["accepted_bdt_crore"] > 0 for k in keep):
        older_cb_pub = max((_omo_pub(x) for x in rows
                            if _omo_pub(x) != latest_pub and x["instrument"] == "CB_REPO" and x["accepted_bdt_crore"] > 0),
                           default=datetime.date.min)
        if older_cb_pub != datetime.date.min:
            target = _prior_free_tuesday(session, d)
            if target is not None:
                for x in (r for r in rows if _omo_pub(r) == older_cb_pub):
                    x2 = dict(x)
                    x2["transaction_date"] = target
                    x2["maturity_date"]    = target + datetime.timedelta(days=(x.get("tenor_days") or 0))
                    saved += insert(x2)
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
                        saved += insert(r)
                else:              # incoming is mis-dated → store it on the gap day
                    for r in keep:
                        r2 = dict(r)
                        r2["transaction_date"] = target
                        r2["maturity_date"]    = target + datetime.timedelta(days=(r.get("tenor_days") or 0))
                        saved += insert(r2)
                log.error("OMO mislabel guard: no-lag release re-homed from 'as on %s' to %s "
                          "— BB double-dated two operations (%s traded but had no OMO).",
                          d, target, target)
                return saved, superseded

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
                        saved += insert(r)
                else:                  # keep stored on d, store incoming CB Repo op on its Tuesday
                    for r in keep:
                        r2 = dict(r)
                        r2["transaction_date"] = target
                        r2["maturity_date"]    = target + datetime.timedelta(days=(r.get("tenor_days") or 0))
                        saved += insert(r2)
                log.error("OMO mislabel guard: CB_REPO operation re-homed from 'as on %s' to "
                          "Tuesday %s — BB double-dated two operations. CONFIRM %s was a "
                          "working day (not a bank holiday).", d, target, target)
                return saved, superseded

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
            saved += insert(r)
    elif not existing:
        for r in keep:
            saved += insert(r)
    elif inc_pub is not None and stored_pub is not None and inc_pub < stored_pub:
        # Incoming release is OLDER than what we already have for this date
        # (e.g. re-seeing the mislabelled 343 after the 345 correction is
        # stored) — never regress: add nothing, remove nothing.
        return saved, superseded
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
                if ex.offered_bdt_crore is None and r.get("offered_bdt_crore") is not None:
                    ex.offered_bdt_crore = r["offered_bdt_crore"]
                if ex.net_bdt_crore is None and r.get("net_bdt_crore") is not None:
                    ex.net_bdt_crore = r["net_bdt_crore"]
                if ex.printed_totals is None and r.get("printed_totals"):
                    ex.printed_totals = r["printed_totals"]
            else:
                saved += insert(r)
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
                     and existing[d].holiday_type not in ("WORKING_DAY", calendar_utils.CIRCULAR_CLOSURE_TYPE)]
        session.commit()
        _load_holidays(session)
        if added or upgraded:                  # a closure learned now must move the dates derived under the old list
            try:
                propagate_calendar_change(session)
            except Exception as exc:
                log.warning("holiday list changed but the dependent dates were not re-rolled: %s", exc)
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
        # A refusal for a release the Doctor has proved is BB's dead link (data_absences, 'Unserved PDF:') is expected: it is
        # logged and retried every run, but it is not this step's failure.
        try:
            from db import DataAbsence
            holes = " ".join((a.reason or "") for a in session.query(DataAbsence).filter(DataAbsence.dataset == "bgiib").all())
        except Exception:
            session.rollback()
            holes = ""
        real_refused = {k: v for k, v in (out["refused"] or {}).items() if not (k.split(":")[0] and k.split(":")[0] in holes)}
        if len(real_refused) != len(out["refused"] or {}):
            log.info("BGIIB: %d refused release(s) are recorded BB dead links (not an error)",
                     len(out["refused"]) - len(real_refused))
        if not rows:
            msg = out.get("reason")
            if real_refused:
                msg = f"{msg}; refused: {real_refused}"
            if msg:
                log.warning("BGIIB: %s", msg)
            return {"rows": 0, "releases": 0, "refused": out["refused"],
                    "errors": [f"bgiib: {msg}"] if real_refused else []}

        now = datetime.datetime.utcnow()

        def _upsert(r: dict) -> str:
            existing = session.query(BgiibAuction).filter_by(
                auction_date=r["auction_date"], tenor_label=r["tenor_label"]).one_or_none()
            if existing is None:
                session.add(BgiibAuction(ingested_utc=now, **r))
                return "added"
            for k, v in r.items():
                setattr(existing, k, v)
            existing.ingested_utc = now
            return "updated"

        # One auction the database refuses must not roll back the others in the batch (see store_rows_isolated).
        res = store_rows_isolated(session, BgiibAuction, rows, "bgiib", now, write=_upsert)
        added, updated = res["kinds"].get("added", 0), res["kinds"].get("updated", 0)
        session.commit()
        log.info("BGIIB: %d release(s) read, %d row(s) added, %d updated",
                 out["releases"], added, updated)
        errs = [f"bgiib: refused {real_refused}"] if real_refused else []
        if res["quarantined"]:
            errs.append(f"bgiib: {len(res['quarantined'])} auction row(s) refused by the database and quarantined: "
                        + "; ".join(f"{q['raw'].get('tenor_label')} {q['raw'].get('auction_date')} "
                                    f"({q['constraint'] or q['error'][:80]})" for q in res["quarantined"][:5]))
        return {"rows": res["stored"], "releases": out["releases"], "added": added,
                "updated": updated, "refused": out["refused"], "errors": errs,
                "blanked": len(res["blanked"]), "quarantined": len(res["quarantined"])}
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
        notices = read_sukuk_notices(session, now)
        errs += notices["errors"]
        return {"rows": len(out["outstanding"]), "added": added, "updated": updated, "notices_added": notices["added"],
                "changed": changed, "documents_added": docs_added, "refused": out["refused"],
                "errors": errs, **watch}
    except Exception as exc:
        session.rollback()
        log.exception("Sukuk fetch failed")
        return {"rows": 0, "errors": [f"sukuk: {exc}"]}
    finally:
        session.close()


BODY_CATEGORIES = ("SUKUK", "AUCTION_NOTICE", "POLICY")   # releases no other step reads; OMO/results/BGIIB/buyback have their own parsers
BODY_RETRY_DAYS = 7                                      # a dead link (BB lists it, never serves it) is retried weekly, not hourly


def store_press_bodies(session, now, categories=BODY_CATEGORIES, limit: int = 6) -> dict:
    """Read each not-yet-read release's PDF ONCE and keep its text in press_releases.body_text.

    Why: parsers are then written and re-run from what we hold, and BB's firewall (which CAPTCHAs repeated fetches, and did
    so after a handful of manual reads) is asked once per release, never per parser change. `limit` newest first per run
    keeps the traffic low; a release that will not download is stored empty and retried after BODY_RETRY_DAYS."""
    from sqlalchemy import text as _t
    out = {"stored": 0, "errors": []}
    cutoff = now - datetime.timedelta(days=BODY_RETRY_DAYS)
    marks = ", ".join(f":c{i}" for i in range(len(categories)))
    params = {f"c{i}": c for i, c in enumerate(categories)}
    params.update({"cut": cutoff, "n": limit})
    pending = session.execute(_t(
        f"SELECT serial, url FROM press_releases WHERE category IN ({marks}) AND (body_text IS NULL OR "
        f"(body_text = '' AND body_fetched_utc < :cut)) ORDER BY published_date DESC LIMIT :n"), params).fetchall()
    if not pending:
        return out
    try:
        from config import BB_PRESS_URL
        from fetchers.bb_session import bb_get_bytes, get_f5_cookies
        import io
        import pdfplumber
        ck, ua = get_f5_cookies(BB_PRESS_URL)
    except Exception as exc:
        out["errors"].append(f"press_bodies: could not reach BB's press releases ({type(exc).__name__}: {exc})")
        return out
    for serial, url in pending:
        body = ""
        try:
            data = bb_get_bytes(url, ck, ua)
            if data[:5].startswith(b"%PDF"):
                with pdfplumber.open(io.BytesIO(data)) as pdf:
                    body = "\n".join((p.extract_text() or "") for p in pdf.pages)
            else:
                out["errors"].append(f"press_bodies {serial}: not a PDF")
        except Exception as exc:
            out["errors"].append(f"press_bodies {serial}: download failed ({type(exc).__name__})")
        session.execute(_t("UPDATE press_releases SET body_text = :b, body_fetched_utc = :t WHERE serial = :s"),
                        {"b": body, "t": now, "s": serial})
        session.commit()
        if body:
            out["stored"] += 1
    return out


def read_sukuk_notices(session, now, limit: int = 10) -> dict:
    """Parse each sukuk press release whose text we hold and whose auction we have not yet stored (BB's sukuk page lists
    an issue only after it settles). A release that will not parse, or whose figures disagree, is REPORTED (an error line
    naming the serial and why), never stored half-read."""
    from sqlalchemy import text as _t
    from fetchers.sukuk_notice import parse_sukuk_notice
    out = {"added": 0, "errors": []}
    got = store_press_bodies(session, now, categories=("SUKUK",), limit=limit)
    out["errors"] += [e.replace("press_bodies", "sukuk_notice", 1) for e in got["errors"]]
    pending = session.execute(_t(
        "SELECT p.serial, p.title, p.url, p.body_text FROM press_releases p WHERE p.category = 'SUKUK' AND "
        "p.body_text IS NOT NULL AND p.body_text <> '' AND NOT EXISTS "
        "(SELECT 1 FROM sukuk_auctions a WHERE a.source_serial = p.serial) ORDER BY p.published_date DESC LIMIT :n"),
        {"n": limit}).fetchall()
    for serial, title, url, body in pending:
        rep = parse_sukuk_notice(body, serial, title or "", url)
        if rep["row"] is None:
            out["errors"].append(f"sukuk_notice {serial}: refused - {rep['refused']}")
            continue
        session.add(SukukAuction(first_seen_utc=now, **rep["row"]))
        session.commit()
        out["added"] += 1
        log.info("Sukuk notice %s: %s, %.0f cr, auction %s", serial, rep["row"]["name"], rep["row"]["face_crore"],
                 rep["row"]["auction_date"])
    return out


COMPARABLE_OMO_DAYS = 30      # every run looks back at least 35 days, so this much is common to all of them


def _omo_watch_counts(txns, meta: dict, today: datetime.date) -> dict:
    """What the source watch should compare between two reads of BB's press-release list.

    The hourly run looks back 35 days and the full run 200, so raw counts differ ~3x between runs of the same
    unchanged page - 'parsed 77, less than half the 219 last seen' paged on exactly that. Only releases inside
    the window every run shares are counted, and rows older than a run's own window are not 'skipped': the
    window did that, not the parser."""
    cutoff = today - datetime.timedelta(days=COMPARABLE_OMO_DAYS)
    parsed = sum(1 for t in txns if isinstance(t.get("transaction_date"), datetime.date)
                 and t["transaction_date"] >= cutoff)
    old = meta.get("skipped_too_old") or 0
    return {"rows_seen": max(0, (meta.get("rows_seen") or 0) - old), "rows_parsed": parsed,
            "not_omo": meta.get("skipped_not_omo") or 0}


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
        counts = _omo_watch_counts(txns, meta, datetime.date.today())
        watch = _register_source(session, SW.fingerprint(
            "omo_press", _OMO_URL, selector=meta.get("selector") or "",
            rows_seen=counts["rows_seen"], rows_parsed=counts["rows_parsed"],
            skips={"not an OMO release": counts["not_omo"]},
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
        press_error = None
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
            # BB announcements (buybacks, auction notices) ride on this listing: not storing it is a failed step, not a warning.
            press_error = f"press listing not stored: {type(exc).__name__}: {str(exc)[:160]}"
        meta.pop("all_links", None)

        if not txns:
            # Empty is a RESULT, not a success. Say why, and say it where the
            # pipeline run records it.
            msg = f"omo: fetched nothing — {reason or 'no reason reported'}"
            log.error(msg)
            return {"rows": 0, "pdfs": 0, "errors": [msg] + ([press_error] if press_error else []),
                    "empty_reason": reason, **watch, **meta}

        store_stats: dict = {}
        saved, superseded = _store_omo_txns(session, txns, datetime.datetime.utcnow(), store_stats)
        session.commit()
        log.info("OMO fetch: %d rows stored, %d date(s) superseded by corrections, from %d txns",
                 saved, superseded, len(txns))
        recon = sorted({t["recon_mismatch"] for t in txns if t.get("recon_mismatch")})
        return {"rows": saved, "txns": len(txns), "pdfs": meta.get("pdfs", max_files),
                "errors": recon + omo_store_errors(store_stats) + ([press_error] if press_error else []),
                "rejected_days": store_stats.get("rejected", []), **watch, **meta}
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
        # A row the database refuses must not roll back the months around it: each row is its own savepoint,
        # blanked on the fields its constraint names, else quarantined (and named in the errors below).
        stored = store_rows_isolated(session, PrimaryYieldSnapshot, rows, "treasury",
                                     write=lambda r: (upsert_primary_yield(session, r), "stored")[1])
        session.commit()
        # One fingerprint per page read. The newest page is the one that matters
        # for "has BB moved today"; the rest are registered so a shape BB uses
        # only on older months is known-good rather than a surprise.
        from fetchers.treasury import LAST_STRUCTURE
        watch, errs = {}, []
        if stored["quarantined"]:
            errs.append(f"treasury: {len(stored['quarantined'])} yield row(s) refused by the database and quarantined: "
                        + "; ".join(f"{q['raw'].get('tenor_label')} {q['raw'].get('auction_date')} ({q['constraint'] or q['error'][:80]})"
                                    for q in stored["quarantined"][:5]))
        if stored["blanked"]:
            errs.append(f"treasury: {len(stored['blanked'])} yield row(s) stored with a field blank because it broke "
                        + ", ".join(sorted({b['constraint'] for b in stored['blanked']})))
        for snap in LAST_STRUCTURE:
            w = _register_source(session, snap)
            if w:
                watch = w
                if w.get("source_verdict") == "degraded":
                    errs.append("treasury page structure: " + "; ".join(w["source_reasons"]))
        log.info("Primary yield history: stored %d rows (%d months)", stored["stored"], months_back)
        return {"rows": stored["stored"], "months": months_back, "errors": errs,
                "blanked": len(stored["blanked"]), "quarantined": len(stored["quarantined"]), **watch}
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
