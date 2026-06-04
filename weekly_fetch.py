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

# Fetch windows — overridable via env. Defaults preserve the original local
# behaviour; the cloud workflow sets a smaller OMO window for fast daily runs
# (the DB already holds history, so daily runs only need to catch new days).
OMO_DAYS_BACK   = int(os.environ.get("OMO_DAYS_BACK", "200"))
OMO_MAX_FILES   = int(os.environ.get("OMO_MAX_FILES", "220"))
TREASURY_MONTHS = int(os.environ.get("TREASURY_MONTHS", "2"))
CALLMONEY_DAYS  = int(os.environ.get("CALLMONEY_DAYS", "90"))
REFRATE_DAYS    = int(os.environ.get("REFRATE_DAYS", "90"))

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

    from db import init_db, fix_all_sequences
    from seeds_loader import load_holiday_file
    from calendar_utils import load_holidays
    from db import get_session, HolidayCalendar
    from engines.pipeline import run_pipeline, run_omo_fetch, run_primary_yield_history

    # ── Init ──────────────────────────────────────────────────────────────────
    init_db()
    fix_all_sequences()   # bulletproof: a stale id-sequence can never block a write

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
    log.info("--- Step 2: OMO fetch (%d days) ---", OMO_DAYS_BACK)
    try:
        result = run_omo_fetch(days_back=OMO_DAYS_BACK, max_files=OMO_MAX_FILES)
        log.info("OMO OK | new_rows=%s", result.get("rows"))
        if result.get("errors"):
            errors.extend(result["errors"])
    except Exception as exc:
        log.exception("OMO fetch failed: %s", exc)
        errors.append(f"omo: {exc}")

    # ── 3. Treasury yield history (last 2 months) ─────────────────────────────
    log.info("--- Step 3: Treasury yield history (%d months) ---", TREASURY_MONTHS)
    try:
        result = run_primary_yield_history(months_back=TREASURY_MONTHS)
        log.info("Treasury OK | new_rows=%s", result.get("rows"))
        if result.get("errors"):
            errors.extend(result["errors"])
    except Exception as exc:
        log.exception("Treasury fetch failed: %s", exc)
        errors.append(f"treasury: {exc}")

    # ── 4. Call money market rates (last 35 days) ────────────────────────────
    log.info("--- Step 4: Call money market rates (%d days) ---", CALLMONEY_DAYS)
    try:
        from fetchers.callmoney import fetch_call_money
        from db import CallMoneyRate
        rows_cm = fetch_call_money(days_back=CALLMONEY_DAYS)
        import datetime as _dt
        now_utc = _dt.datetime.utcnow()
        session = get_session()
        saved_cm = 0
        for r in rows_cm:
            r["ingested_utc"] = now_utc
            if not session.query(CallMoneyRate).filter_by(
                trade_date=r["trade_date"],
                product=r["product"],
                maturity_days=r["maturity_days"],
            ).first():
                session.add(CallMoneyRate(**r))
                saved_cm += 1
        session.commit()
        session.close()
        log.info("Call money OK | new_rows=%d (fetched %d)", saved_cm, len(rows_cm))
    except Exception as exc:
        log.exception("Call money fetch failed: %s", exc)
        errors.append(f"callmoney: {exc}")

    # ── 5. FX auction results ─────────────────────────────────────────────────
    log.info("--- Step 5: FX auction results ---")
    try:
        from fetchers.fx import fetch_fx_auctions
        from db import FxAuctionResult
        import datetime as _dt
        rows_fx = fetch_fx_auctions()
        now_utc = _dt.datetime.utcnow()
        session = get_session()
        saved_fx = 0
        for r in rows_fx:
            r["ingested_utc"] = now_utc
            if not session.query(FxAuctionResult).filter_by(
                auction_date=r["auction_date"],
                auction_type=r["auction_type"],
            ).first():
                session.add(FxAuctionResult(**r))
                saved_fx += 1
        session.commit()
        session.close()
        log.info("FX OK | new_rows=%d (fetched %d)", saved_fx, len(rows_fx))
    except Exception as exc:
        log.exception("FX fetch failed: %s", exc)
        errors.append(f"fx: {exc}")

    # ── 6. Money market reference rates (DOMMR + BOFR, today only) ───────────
    log.info("--- Step 6: Money market reference rates ---")
    try:
        from fetchers.refrate import fetch_refrate
        from db import RefRate
        import datetime as _dt
        rows_rr = fetch_refrate(days_back=REFRATE_DAYS)
        now_utc = _dt.datetime.utcnow()
        session = get_session()
        saved_rr = 0
        for r in rows_rr:
            r["ingested_utc"] = now_utc
            if not session.query(RefRate).filter_by(
                trade_date=r["trade_date"],
                rate_type=r["rate_type"],
                product=r["product"],
            ).first():
                session.add(RefRate(**r))
                saved_rr += 1
        session.commit()
        session.close()
        log.info("RefRate OK | new_rows=%d (fetched %d)", saved_rr, len(rows_rr))
    except Exception as exc:
        log.exception("RefRate fetch failed: %s", exc)
        errors.append(f"refrate: {exc}")

    # ── 7. Wage-earner remittance (monthly, BB econdata) ─────────────────────
    log.info("--- Step 7: Wage-earner remittance ---")
    try:
        from fetchers.remittance import fetch_remittance
        from db import RemittanceMonthly
        import datetime as _dt
        rows_rm = fetch_remittance()
        now_utc = _dt.datetime.utcnow()
        session = get_session()
        saved_rm = 0
        for r in rows_rm:
            existing = session.query(RemittanceMonthly).filter_by(month=r["month"]).first()
            if existing:
                # refresh the latest figures in case BB revised them
                existing.remittance_usd_mn = r["remittance_usd_mn"]
                existing.remittance_bdt_bn = r["remittance_bdt_bn"]
                existing.ingested_utc = now_utc
            else:
                session.add(RemittanceMonthly(ingested_utc=now_utc, **r))
                saved_rm += 1
        session.commit()
        session.close()
        log.info("Remittance OK | new_rows=%d (fetched %d)", saved_rm, len(rows_rm))
    except Exception as exc:
        log.exception("Remittance fetch failed: %s", exc)
        errors.append(f"remittance: {exc}")

    # ── 8. Foreign-exchange reserves (monthly, BB econdata) ──────────────────
    log.info("--- Step 8: FX reserves ---")
    try:
        from fetchers.reserves import fetch_reserves
        from db import ReservesMonthly
        import datetime as _dt
        rows_rs = fetch_reserves()
        now_utc = _dt.datetime.utcnow()
        session = get_session()
        saved_rs = 0
        for r in rows_rs:
            existing = session.query(ReservesMonthly).filter_by(month=r["month"]).first()
            if existing:
                # refresh in case BB revised the figures
                existing.gross_reserves_usd_mn = r["gross_reserves_usd_mn"]
                existing.net_reserves_bpm6_usd_mn = r["net_reserves_bpm6_usd_mn"]
                existing.ingested_utc = now_utc
            else:
                session.add(ReservesMonthly(ingested_utc=now_utc, **r))
                saved_rs += 1
        session.commit()
        session.close()
        log.info("Reserves OK | new_rows=%d (fetched %d)", saved_rs, len(rows_rs))
    except Exception as exc:
        log.exception("Reserves fetch failed: %s", exc)
        errors.append(f"reserves: {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.datetime.now() - start).seconds
    if errors:
        log.warning("Weekly fetch finished with %d error(s) in %ds:", len(errors), elapsed)
        for e in errors:
            log.warning("  - %s", e)
    else:
        log.info("Weekly fetch complete — no errors — %ds elapsed", elapsed)

    # Data-integrity monitor: scan the stored data for rule violations (0% yields,
    # OMO mislabels, etc.) so bad data is caught automatically, not by eye.
    try:
        from validate import integrity_check
        quality = integrity_check()
        if not quality.get("ok"):
            log.warning("DATA INTEGRITY: %d issue(s) found: %s",
                        quality.get("issue_count"), quality.get("issues"))
        else:
            log.info("Data integrity: clean (0 issues)")
    except Exception as exc:
        quality = {"ok": None, "error": str(exc)}
        log.warning("integrity_check failed: %s", exc)

    # Record the run so the dashboard can show "last refreshed at X" honestly,
    # even when a run found no new rows.
    try:
        import json as _json
        from db import PipelineRun
        session = get_session()
        session.add(PipelineRun(
            run_utc=datetime.datetime.utcnow(),
            kind="refresh",
            new_rows=None,
            errors=_json.dumps(errors) if errors else None,
            elapsed_sec=elapsed,
            quality=_json.dumps(quality),
        ))
        session.commit()
        session.close()
    except Exception as exc:
        log.warning("Could not record pipeline run: %s", exc)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
