"""
weekly_fetch.py — Incremental weekly data refresh.

Runs every Saturday 10 PM via Windows Task Scheduler.
Adds new data to the existing dataset — never overwrites historical rows.

What runs:
  1. GSOM pipeline  — securities, coupons, maturities, auctions, daily flows
  2. OMO fetch      — last 14 days of BB press release PDFs (incremental)
  3. Treasury fetch — last 2 months of BB primary yield results (incremental)

Log: logs/weekly_fetch.log
"""
import sys
import os
import datetime
import logging
from pathlib import Path

# Run from project root regardless of where the script is called from
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "weekly_fetch.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def main():
    start = datetime.datetime.now()
    log.info("=" * 60)
    log.info("Weekly fetch started  %s", start.strftime("%Y-%m-%d %H:%M"))
    log.info("=" * 60)

    from db import init_db
    from seeds_loader import load_holiday_file
    from calendar_utils import load_holidays
    from db import get_session, HolidayCalendar
    from engines.pipeline import run_pipeline, run_omo_fetch, run_primary_yield_history

    # ── Init ──────────────────────────────────────────────────────────────────
    init_db()

    seed = ROOT / "data" / "seeds" / "holidays_2025-26.yaml"
    if seed.exists():
        load_holiday_file(str(seed))

    session = get_session()
    rows = session.query(HolidayCalendar).all()
    load_holidays({r.calendar_date for r in rows})
    session.close()

    today = datetime.date.today()
    errors = []

    # ── 1. GSOM pipeline ──────────────────────────────────────────────────────
    log.info("--- Step 1: GSOM pipeline ---")
    try:
        summary = run_pipeline(
            today - datetime.timedelta(days=60),
            today + datetime.timedelta(days=120),
        )
        log.info(
            "Pipeline OK | securities=%s coupons=%s maturities=%s auctions=%s",
            summary.get("securities"), summary.get("coupons"),
            summary.get("maturities"), summary.get("auctions"),
        )
        if summary.get("errors"):
            errors.extend(summary["errors"])
    except Exception as exc:
        log.exception("Pipeline failed: %s", exc)
        errors.append(f"pipeline: {exc}")

    # ── 2. OMO fetch (last 200 days — covers AR 180D + all outstanding positions) ─
    # AR 180D transactions from ~6 months ago are still outstanding today.
    # 14 days misses them. 200 days ensures the full outstanding picture is
    # always complete. Upserts skip already-stored rows so re-fetching is safe.
    log.info("--- Step 2: OMO fetch (200 days) ---")
    try:
        result = run_omo_fetch(days_back=200, max_files=220)
        log.info("OMO OK | new_rows=%s", result.get("rows"))
        if result.get("errors"):
            errors.extend(result["errors"])
    except Exception as exc:
        log.exception("OMO fetch failed: %s", exc)
        errors.append(f"omo: {exc}")

    # ── 3. Treasury yield history (last 2 months) ─────────────────────────────
    log.info("--- Step 3: Treasury yield history (2 months) ---")
    try:
        result = run_primary_yield_history(months_back=2)
        log.info("Treasury OK | new_rows=%s", result.get("rows"))
        if result.get("errors"):
            errors.extend(result["errors"])
    except Exception as exc:
        log.exception("Treasury fetch failed: %s", exc)
        errors.append(f"treasury: {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.datetime.now() - start).seconds
    if errors:
        log.warning("Weekly fetch finished with %d error(s) in %ds:", len(errors), elapsed)
        for e in errors:
            log.warning("  - %s", e)
    else:
        log.info("Weekly fetch complete — no errors — %ds elapsed", elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
