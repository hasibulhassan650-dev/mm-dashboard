"""
forecast/run_forecast.py — weekly cutoff-rate forecasting for BB treasury auctions.

RUNS IN GITHUB ACTIONS ONLY. Never in the API: Vercel's serverless Python is
too small for a stats stack, so all modelling happens here and the result is
written to Supabase. The /api/forecast endpoint and the /research tab only
ever READ these tables.

Phase 1 — pure pandas/numpy, no heavy dependencies:
  Model 0  naive     next cutoff = last cutoff (the benchmark to beat)
  Model 1  momentum  next cutoff = last + beta x last_change,
                     beta from a no-intercept OLS of each change on the one
                     before it (numpy dot products, nothing more)

Skill comes from an expanding-window, one-step-ahead out-of-sample backtest:
refit on everything up to t, predict t, score, roll forward. Errors are in
basis points so a 91D bill and a 20Y bond can be compared on one axis.
The 95% band is +/-1.96 x the model's own OOS RMSE — it is measured forecast
error, not a distributional assumption about yields.

Usage:  python forecast/run_forecast.py            (writes to DATABASE_URL)
        python forecast/run_forecast.py --dry-run  (prints, writes nothing)
"""
import datetime
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import (init_db, get_session, fix_all_sequences,       # noqa: E402
                PrimaryYieldSnapshot, AuctionForecast, BacktestMetric)

log = logging.getLogger("forecast")

# A tenor needs enough prints that an expanding-window backtest still leaves a
# meaningful test set. Bonds auction ~monthly, so ~35 prints since 2023-09 is
# all there is — MIN_TRAIN 12 leaves ~23 scored forecasts. Below MIN_HISTORY
# the metrics would be noise dressed up as skill, so the tenor is skipped.
MIN_HISTORY = 16
MIN_TRAIN   = 12
MODELS      = ("naive", "momentum")
Z95         = 1.96

# primary_yield_snapshots goes back to 2007, but fitting across that whole span
# is misleading here: the 2007-2022 prints come from policy regimes (and a rate
# cap era) with volatility nothing like today's. Fitted on everything, the 91D
# momentum beta picks up old regime shifts and the OOS RMSE — which IS the
# published band — inflates from ~28bps to ~49bps, so the tab would advertise a
# +/-96bps interval on a market currently moving ~10bps a week.
# Default to the current-regime window; override with FORECAST_LOOKBACK_YEARS=0
# to fit the full history.
LOOKBACK_YEARS = float(os.environ.get("FORECAST_LOOKBACK_YEARS", "3"))


# ── models ────────────────────────────────────────────────────────────────────

def momentum_beta(changes: np.ndarray) -> float:
    """No-intercept OLS slope of each change on the previous one.

    beta = sum(x*y) / sum(x*x) for x = changes[:-1], y = changes[1:].
    No intercept on purpose: an intercept would bake a constant drift into
    every forecast, and BB cutoffs mean-revert around policy rather than trend.
    Returns 0.0 when there is nothing to fit, which collapses momentum to naive.
    """
    if len(changes) < 3:
        return 0.0
    x, y = changes[:-1], changes[1:]
    denom = float(np.dot(x, x))
    if denom <= 1e-12:          # a flat series carries no momentum signal
        return 0.0
    return float(np.dot(x, y) / denom)


def predict(train: np.ndarray) -> dict:
    """One-step-ahead point forecast per model, given the history up to now."""
    last = float(train[-1])
    changes = np.diff(train)
    beta = momentum_beta(changes)
    last_change = float(changes[-1]) if len(changes) else 0.0
    return {"naive": last, "momentum": last + beta * last_change}


# ── backtest ──────────────────────────────────────────────────────────────────

def backtest(y: np.ndarray) -> dict:
    """Expanding-window one-step-ahead OOS backtest.

    At each t the models see ONLY y[:t] — no future data touches a fit, so the
    metrics are what a user would actually have got forecasting week by week.
    """
    errs = {m: {"err": [], "pred_chg": [], "act_chg": []} for m in MODELS}
    for t in range(MIN_TRAIN, len(y)):
        train, actual = y[:t], float(y[t])
        last = float(train[-1])
        for m, p in predict(train).items():
            errs[m]["err"].append(p - actual)
            errs[m]["pred_chg"].append(p - last)
            errs[m]["act_chg"].append(actual - last)
    return errs


def score(e: dict) -> dict | None:
    """Turn raw errors into the published metrics. Yields are in %, so x100 = bps."""
    err = np.asarray(e["err"], dtype=float)
    if err.size == 0:
        return None
    pred_chg = np.asarray(e["pred_chg"], dtype=float)
    act_chg = np.asarray(e["act_chg"], dtype=float)
    err_bps = np.abs(err) * 100

    # Naive always predicts zero change, so it makes no directional call to
    # score — dir_acc is NULL for it rather than a fake 0 or 50%.
    called = np.abs(pred_chg) > 1e-9
    dir_acc = (float(np.mean(np.sign(pred_chg[called]) == np.sign(act_chg[called])))
               if called.any() else None)

    return {
        "mae_bps":  round(float(np.mean(err_bps)), 2),
        "rmse_bps": round(float(np.sqrt(np.mean(err ** 2))) * 100, 2),
        "dir_acc":  round(dir_acc, 4) if dir_acc is not None else None,
        "hit_5bps": round(float(np.mean(err_bps <= 5.0)), 4),
        "n_obs":    int(err.size),
    }


# ── data ──────────────────────────────────────────────────────────────────────

def load_history(session, lookback_years: float = LOOKBACK_YEARS) -> pd.DataFrame:
    """Cutoff prints, one row per tenor per auction date, oldest first.

    Trimmed to the last `lookback_years` (0 = everything) — see LOOKBACK_YEARS.
    """
    rows = (session.query(PrimaryYieldSnapshot.auction_date,
                          PrimaryYieldSnapshot.tenor_label,
                          PrimaryYieldSnapshot.security_type,
                          PrimaryYieldSnapshot.cutoff_yield_pct)
            .filter(PrimaryYieldSnapshot.cutoff_yield_pct.isnot(None))
            .filter(PrimaryYieldSnapshot.auction_date.isnot(None))
            .order_by(PrimaryYieldSnapshot.auction_date)
            .all())
    df = pd.DataFrame(rows, columns=["auction_date", "tenor_label",
                                     "security_type", "cutoff_yield_pct"])
    df["auction_date"] = pd.to_datetime(df["auction_date"])
    df["cutoff_yield_pct"] = df["cutoff_yield_pct"].astype(float)
    if lookback_years > 0 and not df.empty:
        cutoff = df["auction_date"].max() - pd.Timedelta(days=round(365.25 * lookback_years))
        df = df[df["auction_date"] >= cutoff]
    return df.drop_duplicates(subset=["tenor_label", "auction_date"], keep="last")


def next_auction_date(dates: pd.Series) -> datetime.date | None:
    """Estimate the next auction date as last date + median recent gap.

    BB publishes a calendar but we don't ingest it yet, so the cadence is read
    off the prints themselves: 7d for bills, ~28d for bonds. The median over the
    last 12 gaps rides out holiday shifts without chasing a single odd week.
    """
    if len(dates) < 2:
        return None
    gaps = dates.diff().dt.days.dropna().tail(12)
    if gaps.empty:
        return None
    gap = int(round(float(gaps.median())))
    if gap <= 0:
        return None
    return (dates.iloc[-1] + datetime.timedelta(days=gap)).date()


# ── persistence ───────────────────────────────────────────────────────────────

def save_forecasts(session, forecasts: list[dict]) -> int:
    """Insert-or-update on (run_date, target_date, tenor, model)."""
    now = datetime.datetime.utcnow()
    n = 0
    for f in forecasts:
        row = session.query(AuctionForecast).filter_by(
            forecast_run_date=f["forecast_run_date"],
            target_auction_date=f["target_auction_date"],
            tenor=f["tenor"], model=f["model"],
        ).first()
        if row:
            for k, v in f.items():
                setattr(row, k, v)
        else:
            session.add(AuctionForecast(created_utc=now, **f))
            n += 1
    session.commit()
    return n


def save_metrics(session, metrics: list[dict]) -> int:
    n = 0
    for m in metrics:
        row = session.query(BacktestMetric).filter_by(
            computed_date=m["computed_date"], model=m["model"], tenor=m["tenor"],
        ).first()
        if row:
            for k, v in m.items():
                setattr(row, k, v)
        else:
            session.add(BacktestMetric(**m))
            n += 1
    session.commit()
    return n


def score_past_forecasts(session, hist: pd.DataFrame) -> int:
    """Backfill actual_yield on forecasts whose target auction has now printed.

    This is what keeps the published track record honest: a forecast is scored
    against the print for its own target date, matched exactly — never against
    the nearest print, which would flatter the model on a shifted auction.
    """
    pending = (session.query(AuctionForecast)
               .filter(AuctionForecast.actual_yield.is_(None))
               .filter(AuctionForecast.target_auction_date.isnot(None))
               .all())
    if not pending:
        return 0
    actual = {(r.tenor_label, r.auction_date.date()): r.cutoff_yield_pct
              for r in hist.itertuples()}
    n = 0
    for f in pending:
        v = actual.get((f.tenor, f.target_auction_date))
        if v is not None:
            f.actual_yield = float(v)
            n += 1
    session.commit()
    return n


# ── main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    today = datetime.date.today()
    session = get_session()
    try:
        hist = load_history(session)
        if hist.empty:
            raise RuntimeError("no cutoff history in primary_yield_snapshots — nothing to model")
        log.info("fitting on %d prints, %s to %s (lookback=%s years)", len(hist),
                 hist["auction_date"].min().date(), hist["auction_date"].max().date(),
                 LOOKBACK_YEARS or "all")

        forecasts, metrics, skipped = [], [], []

        for (tenor, instrument), g in hist.groupby(["tenor_label", "security_type"], sort=True):
            g = g.sort_values("auction_date")
            y = g["cutoff_yield_pct"].to_numpy()
            if len(y) < MIN_HISTORY:
                skipped.append(f"{tenor} (n={len(y)})")
                continue

            target = next_auction_date(g["auction_date"])
            points = predict(y)
            bt = backtest(y)

            for model in MODELS:
                s = score(bt[model])
                if s is None:
                    continue
                band = Z95 * s["rmse_bps"] / 100.0     # bps -> yield %
                point = points[model]
                forecasts.append({
                    "forecast_run_date": today,
                    "target_auction_date": target,
                    "tenor": tenor,
                    "instrument": instrument,
                    "model": model,
                    "point_yield": round(point, 4),
                    "lo_yield": round(point - band, 4),
                    "hi_yield": round(point + band, 4),
                    "actual_yield": None,
                })
                metrics.append({"computed_date": today, "model": model, "tenor": tenor, **s})
                log.info("%-8s %-9s point=%.4f  band=+/-%.0fbps  MAE=%.2f  RMSE=%.2f  "
                         "dir=%s  hit5=%.0f%%  n=%d",
                         tenor, model, point, band * 100, s["mae_bps"], s["rmse_bps"],
                         f"{s['dir_acc']:.0%}" if s["dir_acc"] is not None else "n/a",
                         s["hit_5bps"] * 100, s["n_obs"])

        if skipped:
            log.info("skipped (history < %d prints): %s", MIN_HISTORY, ", ".join(skipped))
        if not forecasts:
            raise RuntimeError("no tenor had enough history to forecast")

        if dry_run:
            log.info("DRY RUN — %d forecasts / %d metrics not written",
                     len(forecasts), len(metrics))
            return {"forecasts": len(forecasts), "metrics": len(metrics), "scored": 0,
                    "dry_run": True}

        scored = score_past_forecasts(session, hist)
        nf = save_forecasts(session, forecasts)
        nm = save_metrics(session, metrics)
        log.info("wrote %d new forecasts (%d total), %d new metrics (%d total), "
                 "scored %d past forecasts", nf, len(forecasts), nm, len(metrics), scored)
        return {"forecasts": len(forecasts), "metrics": len(metrics), "scored": scored,
                "dry_run": False}
    finally:
        session.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    dry_run = "--dry-run" in sys.argv
    if not dry_run:
        if not os.environ.get("DATABASE_URL"):
            log.error("DATABASE_URL is not set — refusing to run against the local SQLite fallback")
            return 1
        init_db()
        fix_all_sequences()
    result = run(dry_run=dry_run)
    log.info("done: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
