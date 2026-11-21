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


def _cal_closure() -> str:
    """The one definition of what counts as a market closure."""
    import calendar_utils
    return calendar_utils.CLOSURE_SQL


LOCK_STALE_SECONDS = 3 * 3600      # a refresh never runs this long; a lock older than this is a crashed run's


# What each step did in THIS run (rows read / stored / refused), reported by the step itself. engines/step_ledger.py turns it,
# with the errors, into one step_runs row per step. A step that reports nothing is not recorded as 'ok': silence is not success.
STEP_STATS: dict = {}


def note_step(step: str, fetched=None, stored=None, rejected=None) -> None:
    STEP_STATS[step] = {"fetched": fetched, "stored": stored, "rejected": rejected}


# The error lines each step's own block appended this run. engines/step_ledger.py files a line under its `<step>:` prefix when that
# names a known step, else under the block that produced it (runners prefix with names of their own, or not at all).
STEP_ERRORS: dict = {}


def _settle(step: str, errors: list, mark: int) -> None:
    """Close a step: EVERY step that ran writes a step_runs row (ok when clean, failed when it errored), so a failure can be seen to
    END. Call it after the step's try/except with `mark = len(errors)` taken before the step. It changes nothing in `errors`; a step
    that already reported its counts (note_step) keeps them, any other is recorded 'ok' with the counts unknown (None, never 0).
    engines/step_ledger.REPORTING_STEPS lists every name used here; tests/test_doctor_open_failures.py keeps the two in step."""
    STEP_ERRORS[step] = [str(e) for e in errors[mark:]]
    STEP_STATS.setdefault(step, {"fetched": None, "stored": None, "rejected": None})


def _stored(st: dict) -> int:
    return (st.get("inserted", 0) + st.get("updated", 0)) if st else 0


def rejected_error(step: str, stats: dict):
    """The error line for a batch in which the database refused some days, or None.

    replace_daily_rows writes each day in its own savepoint, so a refused day no longer loses the others - but a day
    the database refused is still a day that is missing, and it must reach pipeline_runs.errors (and so the digest, the
    sentinel and the Doctor). Only the repo step reported it; four others dropped it into a log line."""
    bad = (stats or {}).get("rejected") or []
    if not bad:
        return None
    return (f"{step}: BB rows for {', '.join(bad)} were rejected by a database rule (the other days were stored)")


def prefixed(step: str, errs) -> list:
    """Errors from a runner that catches its own exceptions arrive bare ("OMO fetch failed ...") and so were not
    attributed to their step: the freshness rule looks for the `<step>:` prefix, and a bare error read as a HEALTHY
    fetch. This makes the attribution uniform."""
    out = []
    for e in errs or []:
        e = str(e)
        out.append(e if e.startswith(f"{step}:") else f"{step}: {e}")
    return out


def _pid_alive(pid: int) -> bool:
    """Is a process with this id running? (os.kill(pid, 0) would TERMINATE it on Windows, so ask the OS instead.)"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259    # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(path: str = None, now: float = None) -> str:
    """Take the one-refresh-at-a-time lock on this machine, or return None if another refresh holds it.

    The PC's hourly and daily refresh tasks can start together; both drive Chrome and collided on its driver
    file. The second one steps aside (the next hourly run does its work) instead of fighting for Chrome."""
    import tempfile
    import time
    path = path or os.path.join(tempfile.gettempdir(), "mm_dashboard_refresh.lock")
    now = time.time() if now is None else now
    try:
        # A lock is stale when it is old, OR when the run that took it is gone. A refresh that was killed (task re-registered,
        # PC slept, Chrome took the console down with it) left its lock behind, and every hourly run for the next three hours
        # then 'stepped aside' for a run that no longer existed.
        try:
            holder = int(open(path).read().strip() or 0)
        except (OSError, ValueError):
            holder = None
        if now - os.path.getmtime(path) > LOCK_STALE_SECONDS or (holder is not None and not _pid_alive(holder)):
            os.remove(path)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return path


def main():
    lock = acquire_lock()
    if lock is None:
        log.warning("Another refresh is already running on this machine - skipping this one "
                    "(the next scheduled run does the work).")
        return
    try:
        _run()
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _run():
    STEP_STATS.clear()
    STEP_ERRORS.clear()
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

    # ── 00. Is BB's website up at all? ───────────────────────────────────────
    # Three plain GETs, a couple of seconds, BEFORE any browser is launched.
    # From 22-Oct-2026 BB served a maintenance page in place of its pages and
    # this job spent half an hour a run waiting for tables that were never
    # coming, then filed every failure as a fault in OUR fetch. The probe is
    # recorded with the run so the guardian can say "BB is down since X" instead
    # of blaming the code — and so it flips back to "ours" the moment BB is up
    # and a fetch still fails. It never skips a step: BB can recover mid-run,
    # and each Chrome step now fails in seconds on its own pre-check anyway.
    bb_status = None
    try:
        from fetchers.bb_health import probe as _bb_probe
        bb_status = _bb_probe()
        if bb_status["state"] in ("maintenance", "partial"):
            log.warning("BB HEALTH: %s — BB's website is serving its maintenance page; "
                        "BB-fed data will not refresh this run", bb_status["state"].upper())
        elif bb_status["state"] == "unreachable":
            log.warning("BB HEALTH: UNREACHABLE - no BB page answered, so browser steps will "
                        "fail fast instead of waiting. This is not maintenance and excuses "
                        "nothing: it may be BB being down or this runner being blocked.")
    except Exception as exc:
        log.warning("BB health probe failed: %s", exc)

    # ── 0. Self-heal — repair the deterministic breakage classes BEFORE fetching.
    # Detection alone left 32 flagged rows sitting untouched for two weeks; every
    # run now fixes what is derivable with certainty and reports the rest.
    healed = {}
    _m = len(errors)
    try:
        from engines.repair import self_heal
        session = get_session()
        healed = self_heal(session)
        session.commit()
        session.close()
        note_step("self_heal", None, healed.get("total", 0))
        if healed.get("total"):
            log.warning("Self-heal repaired %d row(s): %s", healed["total"], healed["repaired"])
            # A repair that MOVES A DATE leaves the ladder stale by
            # construction: daily_net_flow is a materialised aggregate, so the
            # moment a coupon comes off Durga Puja every row it feeds is wrong
            # until this runs. The hourly mode skips the GSOM pipeline, which
            # is where the rebuild used to live — so a healed date would sit
            # unreflected until the next full run. This reads events and
            # writes the aggregate; it touches no network.
            moved = {"coupon_payment_date", "maturity_payment_date",
                     "auction_settlement", "omo-maturity-roll", "yield_auction_date",
                     "auction-sale-value"}
            if moved & set(healed.get("repaired") or {}):
                from engines.pipeline import rebuild_daily_flows
                session = get_session()
                try:
                    n = rebuild_daily_flows(session)
                    log.warning("Self-heal moved dates — rebuilt %d ladder row(s)", n)
                finally:
                    session.close()
    except Exception as exc:
        log.exception("Self-heal failed: %s", exc)
        errors.append(f"self_heal: {exc}")
    _settle("self_heal", errors, _m)

    # ── 3. Treasury yield history — runs BEFORE the GSOM pipeline so today's
    #      auction results are linked to the calendar (confirm_auctions_from_
    #      results) and daily_net_flow is rebuilt with actual accepted amounts
    #      in the same run, not the next one.
    log.info("--- Step 3: Treasury yield history (%d months) ---", TREASURY_MONTHS)
    _m = len(errors)
    try:
        result = run_primary_yield_history(months_back=TREASURY_MONTHS)
        log.info("Treasury OK | new_rows=%s", result.get("rows"))
        note_step("treasury", result.get("rows"), result.get("rows"))
        errors.extend(prefixed("treasury", result.get("errors")))
    except Exception as exc:
        log.exception("Treasury fetch failed: %s", exc)
        errors.append(f"treasury: {exc}")
    _settle("treasury", errors, _m)

    # ── 1. GSOM pipeline ──────────────────────────────────────────────────────
    # Heavy (~27 min: ~7,600 MTM rows + event regen) and BB only updates GSOM
    # end-of-day — so the hourly LIGHT refresh skips it. The 3×/day full runs
    # keep securities/secondary/flows current; light runs reuse that data.
    if LIGHT:
        log.info("--- Step 1: GSOM pipeline — SKIPPED (light/hourly mode) ---")
    else:
        log.info("--- Step 1: GSOM pipeline ---")
        _m = len(errors)
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
        else:
            note_step("pipeline", summary.get("securities"))      # securities processed; what was stored is not reported
        _settle("pipeline", errors, _m)

        # A working day this run (or an earlier one) never saw is re-read from BB's date picker, which still
        # serves past days; it was filled by hand on 31-Oct and must not need a person again.
        _m = len(errors)
        try:
            from engines.pipeline import backfill_secondary_gaps
            _s = get_session()
            try:
                gap = backfill_secondary_gaps(_s)
            finally:
                _s.close()
            if gap["filled"]:
                log.info("Secondary gaps filled: %s", gap["filled"])
            note_step("secondary-gap-fill", None, sum(gap["filled"].values()) if gap["filled"] else 0)
            errors.extend(gap["errors"])
        except Exception as exc:
            log.exception("Secondary gap fill failed: %s", exc)
            errors.append(f"secondary-gap-fill: {exc}")
        _settle("secondary-gap-fill", errors, _m)

    # ── 2. OMO fetch (last 200 days — covers AR 180D + all outstanding positions) ─
    # AR 180D transactions from ~6 months ago are still outstanding today.
    # 14 days misses them. 200 days ensures the full outstanding picture is
    # always complete. Upserts skip already-stored rows so re-fetching is safe.
    log.info("--- Step 2: OMO fetch (%d days) ---", OMO_DAYS_BACK)
    _m = len(errors)
    try:
        result = run_omo_fetch(days_back=OMO_DAYS_BACK, max_files=OMO_MAX_FILES)
        log.info("OMO OK | new_rows=%s", result.get("rows"))
        note_step("omo", result.get("txns", result.get("rows")), result.get("rows"),
                  len(result.get("rejected_days") or []))
        errors.extend(prefixed("omo", result.get("errors")))
    except Exception as exc:
        log.exception("OMO fetch failed: %s", exc)
        errors.append(f"omo: {exc}")
    _settle("omo", errors, _m)

    # ── 4. Call money market rates (last 35 days) ────────────────────────────
    log.info("--- Step 4: Call money market rates (%d days) ---", CALLMONEY_DAYS)
    _m = len(errors)
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
        note_step("callmoney", len(rows_cm), _stored(st), len(st.get("rejected", [])))
        if rejected_error("callmoney", st):
            errors.append(rejected_error("callmoney", st))
    except Exception as exc:
        log.exception("Call money fetch failed: %s", exc)
        errors.append(f"callmoney: {exc}")
    _settle("callmoney", errors, _m)

    # ── 5. FX auction results ─────────────────────────────────────────────────
    log.info("--- Step 5: FX auction results ---")
    _m = len(errors)
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
        note_step("fx", len(rows_fx), saved_fx)
    except Exception as exc:
        log.exception("FX fetch failed: %s", exc)
        errors.append(f"fx: {exc}")
    _settle("fx", errors, _m)

    # ── 6. Money market reference rates (DOMMR + BOFR, today only) ───────────
    log.info("--- Step 6: Money market reference rates ---")
    _m = len(errors)
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
        note_step("refrate", len(rows_rr), _stored(st), len(st.get("rejected", [])))
        if rejected_error("refrate", st):
            errors.append(rejected_error("refrate", st))
    except Exception as exc:
        log.exception("RefRate fetch failed: %s", exc)
        errors.append(f"refrate: {exc}")
    _settle("refrate", errors, _m)

    # ── 7. Wage-earner remittance (monthly, BB econdata) ─────────────────────
    log.info("--- Step 7: Wage-earner remittance ---")
    _m = len(errors)
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
        note_step("remittance", len(rows_rm), saved_rm)
    except Exception as exc:
        log.exception("Remittance fetch failed: %s", exc)
        errors.append(f"remittance: {exc}")
    _settle("remittance", errors, _m)

    # ── 8. Foreign-exchange reserves (monthly, BB econdata) ──────────────────
    log.info("--- Step 8: FX reserves ---")
    _m = len(errors)
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
        note_step("reserves", len(rows_rs), saved_rs)
    except Exception as exc:
        log.exception("Reserves fetch failed: %s", exc)
        errors.append(f"reserves: {exc}")
    _settle("reserves", errors, _m)

    # ── 9. Policy-rate corridor (BB homepage POLICY RATES box) ───────────────
    # Authoritative & self-updating: an MPC rate change is picked up within a
    # day. A CHANGE vs the last stored corridor is logged LOUD (surfaces in the
    # run summary + integrity gate) so it's never silently missed.
    log.info("--- Step 9: Policy-rate corridor ---")
    _m = len(errors)
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
        note_step("policy", 1, 0 if same else 1)
    except Exception as exc:
        log.exception("Policy-rate fetch failed: %s", exc)
        errors.append(f"policy: {exc}")
    _settle("policy", errors, _m)

    # ── 10. Monetary & prices (CPI, bank rates, M2/credit — BB econdata) ─────
    # Monthly indicators; upsert by month so BB revisions are picked up and only
    # real, sourced fields overwrite (a month with rates but no CPI yet must not
    # null out a CPI figure fetched earlier).
    log.info("--- Step 10: Monetary & prices indicators ---")
    _m = len(errors)
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
        note_step("monetary", len(rows_mo), saved_mo)
    except Exception as exc:
        log.exception("Monetary fetch failed: %s", exc)
        errors.append(f"monetary: {exc}")
    _settle("monetary", errors, _m)

    # ── 11. Interbank FX market — spot / forward / swap turnover ─────────────
    # BB's page is a live daily table, so replace-by-date (never insert-only):
    # an insert-only writer froze intraday call-money rows at their first
    # snapshot for weeks before anyone noticed.
    log.info("--- Step 11: Interbank FX market (%d months) ---", IBFX_MONTHS)
    _m = len(errors)
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
        note_step("interbank_fx", len(rows_fx), _stored(st), len(st.get("rejected", [])))
        if rejected_error("interbank_fx", st):
            errors.append(rejected_error("interbank_fx", st))
        # BB skips the occasional working day on this page (08-Oct-2026, while
        # every other feed printed). Record those from THIS fetch's own window
        # so the gap check stops calling BB's omission our defect — and only
        # from a fetch that actually returned a range, never from a failure.
        from sqlalchemy import text as _sa_text
        from engines.absences import confirm_absences
        from engines.pipeline import _load_holidays
        _load_holidays(session)
        import datetime as _d2

        def _asd(v):
            if v is None or hasattr(v, "year"):
                return v
            try:
                return _d2.date.fromisoformat(str(v)[:10])
            except ValueError:
                return None

        hol = {d for d in (_asd(r[0]) for r in session.execute(_sa_text(
            "SELECT calendar_date FROM holiday_calendar WHERE "
            + _cal_closure())).fetchall()) if d is not None}
        gone = confirm_absences(session, "fxmarket",
                                [r.get("trade_date") for r in rows_fx], hol,
                                source="BB interbank FX market page")
        session.commit()
        session.close()
        log.info("Interbank FX OK | %s (fetched %d)%s", st, len(rows_fx),
                 f" | {len(gone)} day(s) confirmed absent at BB" if gone else "")
    except Exception as exc:
        log.exception("Interbank FX fetch failed: %s", exc)
        errors.append(f"interbank_fx: {exc}")
    _settle("interbank_fx", errors, _m)

    # ── 12. Exchange rate of the Taka — all currencies ───────────────────────
    # One snapshot per run; rate_date is the PREVIOUS business day (BB's note),
    # not the publication date — see fetchers/fxrates.py.
    log.info("--- Step 12: Exchange rates (all currencies) ---")
    _m = len(errors)
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
        note_step("fxrates", len(rows_fr), _stored(st), len(st.get("rejected", [])))
        # the page keeps only the latest day, so a refused day here is gone for good: it must never be silent
        if rejected_error("fxrates", st):
            errors.append(rejected_error("fxrates", st))
    except Exception as exc:
        log.exception("FX rates fetch failed: %s", exc)
        errors.append(f"fxrates: {exc}")
    _settle("fxrates", errors, _m)

    # ── 13. Interbank repo — the secured money market ────────────────────────
    log.info("--- Step 13: Interbank repo ---")
    _m = len(errors)
    try:
        from fetchers.interbank_repo import fetch_interbank_repo_range
        from db import InterbankRepo
        from engines.pipeline import replace_daily_rows
        import datetime as _dt
        # Fetch the CURRENT and PREVIOUS month through the page's date picker,
        # not the default view. BB shows only the last FIVE TRADING DAYS there,
        # so a few missed runs lose those days permanently -- which is exactly
        # how 27-30 September went missing. Asking for the month makes a missed
        # run self-healing instead of destructive, and covers month boundaries.
        _today = _dt.date.today()
        _prev = (_today.replace(day=1) - _dt.timedelta(days=1))
        rows_rp = fetch_interbank_repo_range(_prev, _today)
        now_utc = _dt.datetime.utcnow()
        for r in rows_rp:
            r["ingested_utc"] = now_utc
        session = get_session()
        st = replace_daily_rows(session, InterbankRepo, rows_rp, ())
        session.commit()
        session.close()
        log.info("Interbank repo OK | %s (fetched %d)", st, len(rows_rp))
        note_step("interbank_repo", len(rows_rp), _stored(st), len(st.get("rejected", [])))
        if rejected_error("interbank_repo", st):
            errors.append(rejected_error("interbank_repo", st))
        if not rows_rp:
            errors.append("interbank_repo: no rows were read from BB's page (blocked, or the page changed)")
    except Exception as exc:
        log.exception("Interbank repo fetch failed: %s", exc)
        errors.append(f"interbank_repo: {exc}")
    _settle("interbank_repo", errors, _m)

    # BB's press-release listing is NOT fetched as its own step: the OMO step
    # already downloads that exact page and stores the whole classified listing
    # from it. A separate step meant a second Chrome launch per run for data we
    # already had, and Chrome launches are the scarcest thing in this job — six
    # consecutive hourly runs failed on "chrome not reachable" after this step
    # was added. One page, one fetch.

    # ── 13b. Islamic window (BGIIB) ──────────────────────────────────────────
    # Reads the PDFs of BGIIB releases that have no stored auction yet. It
    # fetches no listing of its own — the OMO step records that — and returns
    # immediately when nothing is pending, so in a normal week it costs one
    # PDF download and no browser at all.
    log.info("--- Step 13b: Islamic window (BGIIB) ---")
    _m = len(errors)
    try:
        from engines.pipeline import run_bgiib_fetch
        bg = run_bgiib_fetch()
        log.info("BGIIB OK | %d release(s), %d row(s) (%d new)", bg.get("releases", 0),
                 bg.get("rows", 0), bg.get("added", 0))
        note_step("bgiib", bg.get("rows", 0), bg.get("added", 0))
        errors.extend(prefixed("bgiib", bg.get("errors")))
    except Exception as exc:
        log.exception("BGIIB fetch failed: %s", exc)
        errors.append(f"bgiib: {exc}")
    _settle("bgiib", errors, _m)

    # ── 13c. BGIS Sukuk ──────────────────────────────────────────────────────
    # BB's government sukuk: the outstanding table (a product we held none of)
    # and the notice / prospectus lists, whose new rows are announced to the desk.
    log.info("--- Step 13c: BGIS Sukuk ---")
    # Slow-changing (weekly) and behind F5, which from CI answers with a JavaScript challenge
    # that costs minutes to clear: read it in the full runs (three a day), not every hour, so
    # an hourly run cannot spend its 30-minute budget waiting on a page that changes weekly.
    if LIGHT:
        log.info("Sukuk: skipped in light/hourly mode (read in the full runs)")
    else:
        _m = len(errors)
        try:
            from engines.pipeline import run_sukuk_fetch
            sk = run_sukuk_fetch()
            log.info("Sukuk OK | %d issue(s) (%d new), %d new document(s)", sk.get("rows", 0),
                     sk.get("added", 0), sk.get("documents_added", 0))
            note_step("sukuk", sk.get("rows", 0), sk.get("added", 0))
            for e in sk.get("errors") or []:
                errors.append(e)
            # A new issue, a changed amount or a moved maturity changes cash on specific days:
            # the ladder is a materialised aggregate and is stale until rebuilt.
            if sk.get("added") or sk.get("changed"):
                from engines.pipeline import rebuild_daily_flows
                _s = get_session()
                try:
                    log.warning("Sukuk table changed - rebuilt %d ladder row(s)", rebuild_daily_flows(_s))
                finally:
                    _s.close()
        except Exception as exc:
            log.exception("Sukuk fetch failed: %s", exc)
            errors.append(f"sukuk: {exc}")
        _settle("sukuk", errors, _m)

    # ── 14. BB's gazetted holiday list ───────────────────────────────────────
    # Weekly, because this is the one feed whose VALUE IS IN THE FUTURE: every
    # settlement date, maturity roll and working-day age is computed from it.
    # It went unread until 15-Oct-2026, when the calendar ended 26-Aug and the
    # system did not know that the following Tuesday was Durga Puja. BB posts
    # the next year's list late in December, so a weekly read picks it up
    # within days of publication instead of in January.
    log.info("--- Step 14: BB gazetted holidays ---")
    # The list changes once a year, and F5 answers a datacenter address with a JavaScript
    # challenge that costs minutes to clear. Read it in the full runs (three a day) - an hourly
    # run has no business spending its 30-minute budget on a page that changes in December.
    if LIGHT:
        log.info("Holidays: skipped in light/hourly mode (read in the full runs)")
    else:
        _m = len(errors)
        try:
            from engines.pipeline import run_holiday_fetch
            hol = run_holiday_fetch()
            log.info("Holidays OK | %d parsed, %d added, %d upgraded, %d conflict(s)",
                     hol.get("rows", 0), hol.get("added", 0), hol.get("upgraded", 0),
                     hol.get("conflicts", 0))
            note_step("holidays", hol.get("rows", 0), hol.get("added", 0))
            for e in hol.get("errors") or []:
                errors.append(e)
            if hol.get("refused"):
                errors.append(f"holidays: refused {sum(hol['refused'].values())} row(s) — "
                              f"{hol['refused']}")
        except Exception as exc:
            log.exception("Holiday fetch failed: %s", exc)
            errors.append(f"holidays: {exc}")
        _settle("holidays", errors, _m)

    # ── 14a. Holes in the record that no fetch can fill, written down with their evidence ──
    _m = len(errors)
    try:
        from engines.absences import sync_known_absences, sync_tenor_corrections
        session = get_session()
        try:
            ka = sync_known_absences(session)
            for line in sync_tenor_corrections(session)["corrected"]:
                log.warning("OMO tenor corrected from BB's own maturity print: %s", line)
            session.commit()
        finally:
            session.close()
        for line in ka["recorded"]:
            log.warning("Known absence recorded: %s", line)
        note_step("known_absences", None, len(ka["recorded"]))
        errors.extend(ka["errors"])
    except Exception as exc:
        log.exception("Known absences failed: %s", exc)
        errors.append(f"known_absences: {exc}")
    _settle("known_absences", errors, _m)

    # ── 14b. BB circulars that close the banks ───────────────────────────────
    # The gazetted list does not carry everything: Sharadiya Durga Puja 2026 closed the banks on
    # 20-22 Oct by circular and the calendar only knew 20 and 21. One page, one request; a page that
    # is not served changes nothing and is reported.
    log.info("--- Step 14b: BB closure circulars ---")
    _m = len(errors)
    try:
        from engines.calendar_notices import run_circular_watch
        cw = run_circular_watch()
        log.info("Circulars OK | %d listed, %d applied, %d pending, %d rejected",
                 cw.get("rows", 0), cw.get("applied", 0), cw.get("pending", 0), cw.get("rejected", 0))
        for line in cw.get("lines") or []:
            log.warning("Calendar: %s", line)
        note_step("circulars", cw.get("rows", 0), cw.get("applied", 0))
        for e in cw.get("errors") or []:
            errors.append(e)
    except Exception as exc:
        log.exception("Circular watch failed: %s", exc)
        errors.append(f"circulars: {exc}")
    _settle("circulars", errors, _m)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.datetime.now() - start).seconds
    if errors:
        log.warning("Weekly fetch finished with %d error(s) in %ds:", len(errors), elapsed)
        for e in errors:
            log.warning("  - %s", e)
    else:
        log.info("Weekly fetch complete — no errors — %ds elapsed", elapsed)

    # ORDER MATTERS: the Doctor repairs, and the step ledger is written, BEFORE the verdict below is computed, so the verdict this run
    # records already reflects what was repaired and carries this run's own diagnosis. (They used to run after it, so the banner kept
    # showing a problem the Doctor had fixed until the NEXT hourly run recorded a new verdict.)
    # The Doctor (engines/doctor.py): repairs only what has PROOF (a lost exchange-rate day from our own read log, a tenor BB's own
    # later print contradicts), records each change with its old values, and writes a heartbeat. It never breaks the run.
    try:
        from engines.doctor import run_doctor
        session = get_session()
        dr = run_doctor(session)
        session.commit()
        session.close()
        for line in dr["lost_days"]:
            log.warning("Doctor recorded a lost day: %s", line)
        for f in dr["tenor_fixes"]:
            log.warning("Doctor corrected a tenor: %s %s -> matures %s (proof %s)", f["instrument"], f["deal_date"],
                        f["true_maturity"], f["proof"])
        errors.extend(dr["errors"])
    except Exception as exc:
        log.warning("Doctor did not run: %s", exc)

    # One row per step per machine (engines/step_ledger.py): the ledger the freshness rule and the Doctor judge from. It is
    # written beside, and never instead of, the run row below, and it can never break the run.
    try:
        from engines.step_ledger import record_step_runs
        session = get_session()
        n_steps = record_step_runs(session, errors, STEP_STATS, step_errors=STEP_ERRORS)
        session.commit()
        session.close()
        log.info("Step ledger: %d step row(s) recorded", n_steps)
    except Exception as exc:
        log.warning("Step ledger not written: %s", exc)

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
    if isinstance(quality, dict) and bb_status:
        quality["bb_status"] = bb_status

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
