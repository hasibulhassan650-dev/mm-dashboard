"""load_refrate_html.py — Parse refrate_history.html and upsert into DB.

Usage:
  1. Open Chrome manually, go to the reference rate page
  2. Set your desired date range and submit
  3. Save the page: Ctrl+S -> "Webpage, HTML Only" -> save as refrate_history.html
  4. Run: py -3.14 load_refrate_html.py
"""
import sys, os, datetime, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

from dotenv import load_dotenv
load_dotenv()

from fetchers.refrate import parse_refrate_html
from db import get_session, RefRate, get_engine, Base

Base.metadata.create_all(get_engine(), tables=[RefRate.__table__])

html_file = "refrate_history.html"
if not os.path.exists(html_file):
    print(f"ERROR: {html_file} not found. Save the BB reference rate page first.")
    sys.exit(1)

with open(html_file, encoding="utf-8") as f:
    rows = parse_refrate_html(f.read())

print(f"Parsed {len(rows)} rows")

now = datetime.datetime.utcnow()
session = get_session()
for r in rows:
    r["ingested_utc"] = now
# One canonical daily-series writer: make the DB match the page for these dates
# (insert-only storage is what froze intraday rows at their first snapshot).
from engines.pipeline import replace_daily_rows
_st = replace_daily_rows(session, RefRate, rows, ("rate_type", "product"))
saved, skipped = _st["inserted"] + _st["updated"], _st["unchanged"]

session.commit()
from sqlalchemy import text
count  = session.query(RefRate).count()
latest = session.execute(text("SELECT MAX(trade_date) FROM ref_rates")).scalar()
oldest = session.execute(text("SELECT MIN(trade_date) FROM ref_rates")).scalar()
session.close()
print(f"Saved {saved}, skipped {skipped}. Total: {count} rows, {oldest} to {latest}")
