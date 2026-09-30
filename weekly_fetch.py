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
# Light mode (hourly refresh): skip the heavy end-of-day GSOM securities scrape,
# run only the intraday-relevant feeds (OMO, treasury, call money, FX, ref
# rates, remittance, reserves, policy). Set FETCH_MODE=light.
LIGHT           = os.environ.get("FETCH_MODE", "full").lower() == "light"
OMO_DAYS_BACK   = int(os.environ.get("OMO_DAYS_BACK", "200"))
OMO_MAX_FILES   = int(os.environ.get("OMO_MAX_FILES", "220"))
TREASURY_MONTHS = int(os.environ.get("TREASURY_MONTHS", "2"))
CALLMONEY_DAYS  = int(os.environ.get("CALLMONEY_DAYS", "90"))
REFRATE_DAYS    = int(os.environ.get("REFRATE_DAYS", "90"))
IBFX_MONTHS     = int(os.environ.get("IBFX_MONTHS", "1"))   # interbank FX history depth per run

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
    from calendar_utils import load_calendar_rows
    from db import get_session, HolidayCalendar
    from engines.pipeline import run_pipeline, run_omo_fetch, run_primary_yield_history

    # ── Init ──────────────────────────────────────────────────────────────────
    init_db()
    fix_all_sequences()   # bulletproof: a stale id-sequence can never block a write

    # Every fiscal-year seed file, not just one: the 2026-27 closures (1-Jul
    # bank closing, 5-Aug, 26-Aug) were reaching the DB only by hand.
    for seed in sorted((ROOT / "data" / "seeds").glob("holidays_*.yaml")):
        load_holiday_file(str(seed))

    session = get_session()
    rows = session.query(HolidayCalendar).all()
    load_calendar_rows(rows)
    session.close()

    today = datetime.date.today()
    errors = []

    # ── 0. Self-heal — repair the deterministic breakage classes BEFORE fetching.
    # Detection alone left 32 flagged rows sitting untouched for two weeks; every
    # run now fixes what is derivable with certainty and reports the rest.
    healed = {}
    try:
        from engines.repair import self_heal
        session = get_session()
        healed = self_heal(session)
        session.commit()
        session.close()
        if healed.get("total"):
            log.warning("Self-heal repaired %d row(s): %s", healed["total"], healed["repaired"])
    except Exception as exc:
        log.exception("Self-heal failed: %s", exc)
        errors.append(f"self_heal: {exc}")

    # ── 3. Treasury yield history — runs BEFORE the GSOM pipeline so today's
    #      auction results are linked to the calendar (confirm_auctions_from_
    #      results) and daily_net_flow is rebuilt with actual accepted amounts
    #      in the same run, not the next one.
    log.info("--- Step 3: Treasury yield history (%d months) ---", TREASURY_MONTHS)
    try:
        result = run_primary_yield_history(months_back=TREASURY_MONTHS)
        log.info("Treasury OK | new_rows=%s", result.get("rows"))
        if result.get("errors"):
            errors.extend(result["errors"])
    except Exception as exc:
        log.exception("Treasury fetch failed: %s", exc)
        errors.append(f"treasury: {exc}")

    # ── 1. GSOM pipeline ──────────────────────────────────────────────────────
    # Heavy (~27 min: ~7,600 MTM rows + event regen) and BB only updates GSOM
    # end-of-day — so the hourly LIGHT refresh skips it. The 3×/day full runs
    # keep securities/secondary/flows current; light runs reuse that data.
    if LIGHT:
        log.info("--- Step 1: GSOM pipeline — SKIPPED (light/hourly mode) ---")
    else:
        log.info("--- Step 1: GSOM pipeline ---")
        try:
            # +400d: every stored future flow row is rebuilt each run. With
            # +120d the Feb–Jul 2027 rows (written once by an older +365 run)
            # went stale — 13 days showed half the principal now scheduled.
            summary = run_pipeline(
                today - datetime.timedelta(days=60),
                today + datetime.timedelta(days=400),
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

    # ── 4. Call money market rates (last 35 days) ────────────────────────────
    log.info("--- Step 4: Call money market rates (%d days) ---", CALLMONEY_DAYS)
    try:
        from fetchers.callmoney import fetch_call_money
        from db import CallMoneyRate
        from engines.pipeline import replace_daily_rows
        rows_cm = fetch_call_money(days_back=CALLMONEY_DAYS)
        import datetime as _dt
        now_utc = _dt.datetime.utcnow()
        for r in rows_cm:
            r["ingested_utc"] = now_utc
        session = get_session()
        # BB's page is a running intraday table — the DB must FOLLOW it, never
        # freeze the first snapshot (see replace_daily_rows).
        st = replace_daily_rows(session, CallMoneyRate, rows_cm, ("product", "maturity_days"))
        session.commit()
        session.close()
        log.info("Call money OK | %s (fetched %d)", st, len(rows_cm))
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
        from engines.pipeline import replace_daily_rows
        rows_rr = fetch_refrate(days_back=REFRATE_DAYS)
        now_utc = _dt.datetime.utcnow()
        for r in rows_rr:
            r["ingested_utc"] = now_utc
        session = get_session()
        st = replace_daily_rows(session, RefRate, rows_rr, ("rate_type", "product"))
        session.commit()
        session.close()
        log.info("RefRate OK | %s (fetched %d)", st, len(rows_rr))
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

    # ── 9. Policy-rate corridor (BB homepage POLICY RATES box) ───────────────
    # Authoritative & self-updating: an MPC rate change is picked up within a
    # day. A CHANGE vs the last stored corridor is logged LOUD (surfaces in the
    # run summary + integrity gate) so it's never silently missed.
    log.info("--- Step 9: Policy-rate corridor ---")
    try:
        from fetchers.policy_rates import fetch_policy_rates
        from db import PolicyRateSnapshot
        import datetime as _dt
        pr = fetch_policy_rates()
        if not pr:
            raise RuntimeError("policy fetch returned nothing (page unparseable)")
        today = _dt.date.today()
        session = get_session()
        latest = session.query(PolicyRateSnapshot).order_by(
            PolicyRateSnapshot.first_seen_date.desc(), PolicyRateSnapshot.id.desc()).first()
        same = latest and all(
            abs((getattr(latest, k) or -1) - (pr[k] if pr[k] is not None else -1)) < 1e-9
            for k in ("repo", "slf", "sdf", "bank_rate"))
        if same:
            latest.last_seen_date = today
            latest.ingested_utc = _dt.datetime.utcnow()
            session.commit()
            log.info("Policy corridor unchanged | repo=%s SLF=%s SDF=%s", pr["repo"], pr["slf"], pr["sdf"])
        else:
            if latest:
                log.warning("POLICY RATE CHANGE DETECTED | was repo=%s/SLF=%s/SDF=%s -> now repo=%s/SLF=%s/SDF=%s",
                            latest.repo, latest.slf, latest.sdf, pr["repo"], pr["slf"], pr["sdf"])
                errors.append(f"POLICY RATE CHANGE: repo {latest.repo}->{pr['repo']}, "
                              f"SLF {latest.slf}->{pr['slf']}, SDF {latest.sdf}->{pr['sdf']} — verify & note the MPC date")
            session.add(PolicyRateSnapshot(
                first_seen_date=today, last_seen_date=today,
                repo=pr["repo"], slf=pr["slf"], sdf=pr["sdf"], bank_rate=pr["bank_rate"],
                crr=pr.get("crr"), slr=pr.get("slr"),
                source_last_update=pr.get("source_last_update"), source=pr.get("source"),
                ingested_utc=_dt.datetime.utcnow()))
            session.commit()
            log.info("Policy corridor stored | repo=%s SLF=%s SDF=%s bank=%s", pr["repo"], pr["slf"], pr["sdf"], pr["bank_rate"])
        session.close()
    except Exception as exc:
        log.exception("Policy-rate fetch failed: %s", exc)
        errors.append(f"policy: {exc}")

    # ── 10. Monetary & prices (CPI, bank rates, M2/credit — BB econdata) ─────
    # Monthly indicators; upsert by month so BB revisions are picked up and only
    # real, sourced fields overwrite (a month with rates but no CPI yet must not
    # null out a CPI figure fetched earlier).
    log.info("--- Step 10: Monetary & prices indicators ---")
    try:
        from fetchers.monetary import fetch_monetary
        from db import MonetaryMonthly
        import datetime as _dt
        _MO_FIELDS = ("cpi_p2p", "cpi_12mo_avg", "m2_growth", "reserve_money_growth",
                      "private_credit_growth", "wavg_deposit", "wavg_lending")
        rows_mo = fetch_monetary()
        now_utc = _dt.datetime.utcnow()
        session = get_session()
        saved_mo = 0
        for r in rows_mo:
            present = {k: r[k] for k in _MO_FIELDS if r.get(k) is not None}
            existing = session.query(MonetaryMonthly).filter_by(month=r["month"]).first()
            if existing:
                for k, v in present.items():
                    setattr(existing, k, v)          # only overwrite fields we actually have
                existing.source = "BB econdata"
                existing.ingested_utc = now_utc
            else:
                session.add(MonetaryMonthly(month=r["month"], source="BB econdata",
                                            ingested_utc=now_utc, **present))
                saved_mo += 1
        session.commit()
        session.close()
        log.info("Monetary OK | new_rows=%d (fetched %d)", saved_mo, len(rows_mo))
    except Exception as exc:
        log.exception("Monetary fetch failed: %s", exc)
        errors.append(f"monetary: {exc}")

    # ── 11. Interbank FX market — spot / forward / swap turnover ─────────────
    # BB's page is a live daily table, so replace-by-date (never insert-only):
    # an insert-only writer froze intraday call-money rows at their first
    # snapshot for weeks before anyone noticed.
    log.info("--- Step 11: Interbank FX market (%d months) ---", IBFX_MONTHS)
    try:
        from fetchers.interbank_fx import fetch_interbank_fx
        from db import InterbankFx
        from engines.pipeline import replace_daily_rows
        import datetime as _dt
        rows_fx = fetch_interbank_fx(months_back=IBFX_MONTHS)
        now_utc = _dt.datetime.utcnow()
        for r in rows_fx:
            r["ingested_utc"] = now_utc
        session = get_session()
        st = replace_daily_rows(session, InterbankFx, rows_fx, ("segment",))
        session.commit()
        session.close()
        log.info("Interbank FX OK | %s (fetched %d)", st, len(rows_fx))
    except Exception as exc:
        log.exception("Interbank FX fetch failed: %s", exc)
        errors.append(f"interbank_fx: {exc}")

    # ── 12. Exchange rate of the Taka — all currencies ───────────────────────
    # One snapshot per run; rate_date is the PREVIOUS business day (BB's note),
    # not the publication date — see fetchers/fxrates.py.
    log.info("--- Step 12: Exchange rates (all currencies) ---")
    try:
        from fetchers.fxrates import fetch_fx_rates
        from db import FxRateDaily
        from engines.pipeline import replace_daily_rows
        import datetime as _dt
        rows_fr = fetch_fx_rates()
        now_utc = _dt.datetime.utcnow()
        for r in rows_fr:
            r["ingested_utc"] = now_utc
        session = get_session()
        st = replace_daily_rows(session, FxRateDaily, rows_fr, ("currency",),
                                date_field="rate_date")
        session.commit()
        session.close()
        log.info("FX rates OK | %s (fetched %d)", st, len(rows_fr))
    except Exception as exc:
        log.exception("FX rates fetch failed: %s", exc)
        errors.append(f"fxrates: {exc}")

    # ── 13. Interbank repo — the secured money market ────────────────────────
    log.info("--- Step 13: Interbank repo ---")
    try:
        from fetchers.interbank_repo import fetch_interbank_repo
        from db import InterbankRepo
        from engines.pipeline import replace_daily_rows
        import datetime as _dt
        rows_rp = fetch_interbank_repo()
        now_utc = _dt.datetime.utcnow()
        for r in rows_rp:
            r["ingested_utc"] = now_utc
        session = get_session()
        st = replace_daily_rows(session, InterbankRepo, rows_rp, ())
        session.commit()
        session.close()
        log.info("Interbank repo OK | %s (fetched %d)", st, len(rows_rp))
    except Exception as exc:
        log.exception("Interbank repo fetch failed: %s", exc)
        errors.append(f"interbank_repo: {exc}")

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
    if isinstance(quality, dict) and healed:
        quality["repaired"] = healed.get("repaired") or {}
        quality["repair_review"] = healed.get("skipped") or []

    # Fold this run's verdict into the guardian ledger, so every issue carries a
    # first-seen date and an age instead of being overwritten each run — and
    # persist whatever the self-heal changed, so automatic corrections stay
    # auditable after the fact.
    try:
        from engines.guardian import record, record_repairs
        session = get_session()
        led = record(session, quality if isinstance(quality, dict) else {})
        changes = record_repairs(session, healed)
        session.commit()
        session.close()
        if isinstance(quality, dict):
            quality["ledger"] = led
        log.info("Guardian ledger: %s | %d repair(s) logged", led, changes)
    except Exception as exc:
        log.exception("Guardian ledger failed: %s", exc)
        errors.append(f"guardian: {exc}")

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
