"""
backfill_treasury.py — One-time backfill of BB primary-auction yields from
January 2007 to the current month, into the production DB.

Resumable: a local progress file records completed months, so re-running
continues where it left off. Daily cloud refresh keeps it current afterward.

Usage:
  python backfill_treasury.py            # full backfill 2007-01 -> today
  python backfill_treasury.py probe      # fetch Jan 2007 only (sanity check)
  python backfill_treasury.py 2007 2010  # backfill an explicit year range
"""
import sys
import os
import datetime
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("backfill")

from db import init_db, get_session, PrimaryYieldSnapshot
from fetchers.treasury import fetch_primary_yields_months, month_range

PROGRESS = ROOT / "treasury_backfill_done.txt"
CHUNK_MONTHS = 12   # months per Chrome session (≈6 page loads)


def done_months_from_db() -> set:
    """Months that already have auctions in the DB (by auction_date) — the
    source of truth for resumability (survives ephemeral cloud runs)."""
    from sqlalchemy import text
    s = get_session()
    try:
        rows = s.execute(text("SELECT DISTINCT auction_date FROM primary_yield_snapshots")).fetchall()
        out = set()
        for r in rows:
            d = r[0]
            if d:
                out.add(f"{d.year}-{d.month:02d}")
        return out
    finally:
        s.close()


def fix_sequence():
    """Re-sync the Postgres id sequence to MAX(id) (it can lag after bulk loads,
    causing 'duplicate key (id)=1' on insert). No-op on SQLite."""
    from sqlalchemy import text
    s = get_session()
    try:
        if "postgresql" in str(s.bind.url):
            s.execute(text(
                "SELECT setval(pg_get_serial_sequence('primary_yield_snapshots','id'), "
                "GREATEST((SELECT COALESCE(MAX(id),1) FROM primary_yield_snapshots),1))"
            ))
            s.commit()
            log.info("id sequence re-synced to MAX(id)")
    except Exception as e:
        log.warning("sequence fix skipped: %s", e)
    finally:
        s.close()


def upsert(rows) -> int:
    if not rows:
        return 0
    now = datetime.datetime.utcnow()
    session = get_session()
    saved = 0
    try:
        for r in rows:
            r["ingested_utc"] = now
            exists = session.query(PrimaryYieldSnapshot).filter_by(
                tenor_label=r["tenor_label"], auction_date=r["auction_date"]
            ).first()
            if not exists:
                session.add(PrimaryYieldSnapshot(**r))
                saved += 1
        session.commit()
    finally:
        session.close()
    return saved


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main():
    init_db()
    fix_sequence()
    args = sys.argv[1:]

    if args and args[0] == "probe":
        rows = fetch_primary_yields_months([("January, 2007", datetime.date(2007, 1, 28))])
        log.info("PROBE Jan-2007: %d rows", len(rows))
        for r in rows[:20]:
            log.info("  %s | %s | %s | cutoff=%s offered=%s accepted=%s",
                     r["auction_date"], r["security_type"], r["tenor_label"],
                     r["cutoff_yield_pct"], r["offered_bdt_crore"], r["accepted_bdt_crore"])
        saved = upsert(rows)
        log.info("PROBE saved %d new rows", saved)
        return

    if len(args) == 2:
        start = datetime.date(int(args[0]), 1, 1)
        end = datetime.date(int(args[1]), 12, 28)
    else:
        start = datetime.date(2007, 1, 1)
        end = datetime.date.today()

    all_months = month_range(start, min(end, datetime.date.today()))
    done = done_months_from_db()
    pending = [(m, s) for (m, s) in all_months if f"{s.year}-{s.month:02d}" not in done]
    log.info("Backfill %s → %s: %d months total, %d already in DB, %d pending",
             start, end, len(all_months), len(all_months) - len(pending), len(pending))

    total_saved = 0
    for ci, chunk in enumerate(chunks(pending, CHUNK_MONTHS)):
        log.info("=== chunk %d/%d: %s … %s ===", ci + 1,
                 (len(pending) + CHUNK_MONTHS - 1) // CHUNK_MONTHS, chunk[0][0], chunk[-1][0])
        rows = fetch_primary_yields_months(chunk)
        saved = upsert(rows)
        total_saved += saved
        log.info("  chunk done: fetched %d rows, saved %d new (running total %d)", len(rows), saved, total_saved)

    # report DB coverage
    session = get_session()
    try:
        cnt = session.query(PrimaryYieldSnapshot).count()
        mn = session.query(PrimaryYieldSnapshot).order_by(PrimaryYieldSnapshot.auction_date.asc()).first()
        mx = session.query(PrimaryYieldSnapshot).order_by(PrimaryYieldSnapshot.auction_date.desc()).first()
        log.info("DONE. Total snapshots in DB: %d (earliest %s, latest %s)", cnt,
                 mn.auction_date if mn else "—", mx.auction_date if mx else "—")
    finally:
        session.close()


if __name__ == "__main__":
    main()
