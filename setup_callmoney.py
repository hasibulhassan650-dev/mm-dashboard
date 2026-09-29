"""
setup_callmoney.py — Create call_money_rates table in Supabase and fetch last 30 days.

Run: py setup_callmoney.py
"""
import sys, os, logging, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from db import get_engine, Base, CallMoneyRate, get_session

# ── Step 1: create table in Supabase ─────────────────────────────────────────
log.info("Creating call_money_rates table if not exists…")
engine = get_engine()
Base.metadata.create_all(engine, tables=[CallMoneyRate.__table__])
log.info("Table ready.")

# ── Step 2: fetch 30 days from BB ─────────────────────────────────────────────
log.info("Fetching call money rates (last 30 days)…")
from fetchers.callmoney import fetch_call_money
rows = fetch_call_money(days_back=30)
log.info("Fetched %d rows", len(rows))

if not rows:
    log.warning("No rows fetched — check Chrome / network")
    sys.exit(1)

# ── Step 3: upsert into Supabase ──────────────────────────────────────────────
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite    import insert as sq_insert

now = datetime.datetime.utcnow()
session = get_session()
saved = 0
skipped = 0

for r in rows:
    r["ingested_utc"] = now
    existing = session.query(CallMoneyRate).filter_by(
        trade_date    = r["trade_date"],
        product       = r["product"],
        maturity_days = r["maturity_days"],
    ).first()
    if existing:
        skipped += 1
        continue
    session.add(CallMoneyRate(**r))
    saved += 1

session.commit()
session.close()
log.info("Saved %d new rows, skipped %d existing", saved, skipped)

# ── Step 4: quick sanity check ────────────────────────────────────────────────
session = get_session()
count = session.query(CallMoneyRate).count()
latest = session.execute(text("SELECT MAX(trade_date) FROM call_money_rates")).scalar()
session.close()
log.info("call_money_rates: %d total rows, latest date = %s", count, latest)
