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
                PrimaryYieldSnapshot, AuctionForecast, BacktestMetric,
                BacktestPrediction)
from forecast.features import build as build_features, ols_fit, ols_predict, FEATURE_COLS  # noqa: E402

log = logging.getLogger("forecast")

# A tenor needs enough prints that an expanding-window backtest still leaves a
# meaningful test set. Bonds auction ~monthly, so ~35 prints since 2023-09 is
# all there is — MIN_TRAIN 12 leaves ~23 scored forecasts. Below MIN_HISTORY
# the metrics would be noise dressed up as skill, so the tenor is skipped.
MIN_HISTORY = 16
MIN_TRAIN   = 12
MODELS      = ("naive", "momentum", "ols")
Z95         = 1.96

# Model 2 (OLS on the Phase-2 factors) carries 6 parameters, so it needs a
# deeper training window than the 2-parameter momentum model before its
# coefficients mean anything. Guide Phase 6 suggests 60; that is impossible for
# bonds (~35 prints total), so OLS simply does not run on the short series
# rather than being fitted on nonsense.
MIN_TRAIN_OLS = 40

# A tenor must yield at least this many scored out-of-sample forecasts before
# its metrics are published. Below it the MAE/RMSE are noise wearing a decimal
# point — better to show the tenor no numbers than misleading ones.
MIN_SCORED = 10

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


def predict(train: pd.DataFrame, row: pd.Series) -> dict:
    """One-step-ahead point forecast per model.

    `train` is every auction strictly before the one being forecast; `row` is
    the target auction, from which ONLY the lagged feature columns are read —
    never its own cutoff. All three models predict the same quantity (the next
    cutoff level) so their errors are directly comparable.
    """
    last = float(row["cutoff_lag1"])

    # Model 1 — momentum: regress each change on the previous change.
    beta = momentum_beta(train["d_cutoff"].to_numpy(dtype=float))
    out = {
        "naive": last,
        "momentum": last + beta * float(row["d_cutoff_lag1"]),
    }

    # Model 2 — OLS on the Phase-2 factor set, predicting the CHANGE.
    if len(train) >= MIN_TRAIN_OLS:
        coef = ols_fit(train[FEATURE_COLS].to_numpy(dtype=float),
                       train["d_cutoff"].to_numpy(dtype=float))
        if coef is not None:
            out["ols"] = last + ols_predict(coef, row[FEATURE_COLS].to_numpy(dtype=float))
    return out


# ── backtest ──────────────────────────────────────────────────────────────────

def backtest(f: pd.DataFrame) -> dict:
    """Expanding-window one-step-ahead OOS backtest over the feature frame.

    At each t the models see ONLY rows before t — no future data touches a fit,
    so the metrics are what the desk would actually have got week by week.
    """
    errs = {m: {"err": [], "pred_chg": [], "act_chg": [], "date": [],
                "actual": [], "pred": []} for m in MODELS}
    for t in range(MIN_TRAIN, len(f)):
        train = f.iloc[:t]
        row = f.iloc[t]
        actual = float(row["cutoff_yield_pct"])
        last = float(row["cutoff_lag1"])
        for m, p in predict(train, row).items():
            e = errs[m]
            e["err"].append(p - actual)
            e["pred_chg"].append(p - last)
            e["act_chg"].append(actual - last)
            e["date"].append(row["auction_date"])
            e["actual"].append(actual)
            e["pred"].append(p)
    return errs


def diebold_mariano(err_model: np.ndarray, err_naive: np.ndarray) -> float | None:
    """Two-sided DM p-value on squared-error loss, model vs naive.

    Answers the question the guide insists on: is the improvement real, or
    luck? Uses a Newey-West (Bartlett) long-run variance of the loss
    differential so serial correlation in the errors does not inflate
    significance, and a normal reference distribution — so no scipy needed and
    the weekly job stays dependency-free.
    """
    n = len(err_model)
    if n < 20 or n != len(err_naive):
        return None
    d = err_naive ** 2 - err_model ** 2          # >0 means the model is better
    dbar = float(np.mean(d))
    dc = d - dbar
    lag = int(np.floor(4 * (n / 100) ** (2 / 9))) or 1
    gamma0 = float(np.dot(dc, dc) / n)
    lrv = gamma0
    for k in range(1, lag + 1):
        cov = float(np.dot(dc[k:], dc[:-k]) / n)
        lrv += 2 * (1 - k / (lag + 1)) * cov
    if lrv <= 0:
        return None
    dm = dbar / np.sqrt(lrv / n)
    # two-sided normal tail via the erf-based CDF
    from math import erf, sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(dm) / sqrt(2))))
    return round(float(p), 4)


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
                          PrimaryYieldSnapshot.cutoff_yield_pct,
                          PrimaryYieldSnapshot.offered_bdt_crore,
                          PrimaryYieldSnapshot.accepted_bdt_crore)
            .filter(PrimaryYieldSnapshot.cutoff_yield_pct.isnot(None))
            .filter(PrimaryYieldSnapshot.auction_date.isnot(None))
            .order_by(PrimaryYieldSnapshot.auction_date)
            .all())
    df = pd.DataFrame(rows, columns=["auction_date", "tenor_label", "security_type",
                                     "cutoff_yield_pct", "offered_bdt_crore",
                                     "accepted_bdt_crore"])
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


def _next_row(f: pd.DataFrame) -> pd.Series:
    """The feature row for the NEXT (unheld) auction.

    Its lag-1 inputs are the latest auction's realised values, so they shift up
    by one relative to the last frame row: what was "this auction" becomes
    "t-1". Rebuilding them from the raw series is what keeps the live forecast
    on exactly the same footing as every backtested one.
    """
    last = f.iloc[-1]
    cut = f["cutoff_yield_pct"].astype(float)
    trail = cut.tail(8)
    return pd.Series({
        "cutoff_lag1":    float(last["cutoff_yield_pct"]),
        "d_cutoff_lag1":  float(last["d_cutoff"]),
        "cover_lag1":     float(last["cover_now"]),
        "d_cover_lag1":   float(last["cover_now"] - last["cover_lag1"]),
        "size_lag1":      float(last["size_now"]),
        "level_gap_lag1": float(cut.iloc[-1] - trail.mean()),
    })


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


def save_predictions(session, preds: list[dict]) -> int:
    """Persist the backtest's out-of-sample predictions for the history chart.

    Rewrites this run's rows for the tenors involved rather than accumulating,
    so the table always reflects the current model definitions.
    """
    n = 0
    for p in preds:
        row = session.query(BacktestPrediction).filter_by(
            computed_date=p["computed_date"], tenor=p["tenor"],
            model=p["model"], auction_date=p["auction_date"],
        ).first()
        if row:
            row.actual_yield, row.pred_yield = p["actual_yield"], p["pred_yield"]
        else:
            session.add(BacktestPrediction(**p))
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

        forecasts, metrics, preds, skipped = [], [], [], []

        for (tenor, instrument), g in hist.groupby(["tenor_label", "security_type"], sort=True):
            g = g.sort_values("auction_date")
            if len(g) < MIN_HISTORY:
                skipped.append(f"{tenor} (n={len(g)})")
                continue

            f = build_features(g)
            if len(f) - MIN_TRAIN < MIN_SCORED:
                skipped.append(f"{tenor} (only {max(0, len(f) - MIN_TRAIN)} scorable)")
                continue

            target = next_auction_date(g["auction_date"])
            bt = backtest(f)

            # The live forecast: train on EVERY usable row, then predict the
            # next auction from a synthetic row carrying only lagged inputs.
            live_row = _next_row(f)
            points = predict(f, live_row)

            naive_err = np.asarray(bt["naive"]["err"], dtype=float)

            for model in MODELS:
                if model not in points:
                    continue
                s = score(bt[model])
                if s is None:
                    continue
                if model != "naive":
                    # OLS starts later than naive (bigger training requirement),
                    # so the two error series must be aligned on the auctions
                    # BOTH models actually scored before they can be compared.
                    shared = set(bt[model]["date"])
                    idx = [i for i, d in enumerate(bt["naive"]["date"]) if d in shared]
                    s["dm_pvalue"] = diebold_mariano(
                        np.asarray(bt[model]["err"], dtype=float), naive_err[idx])
                band = Z95 * s["rmse_bps"] / 100.0     # bps -> yield %
                point = points[model]
                e = bt[model]
                preds.extend({
                    "computed_date": today, "tenor": tenor, "model": model,
                    "auction_date": d.date() if hasattr(d, "date") else d,
                    "actual_yield": round(a, 4), "pred_yield": round(p, 4),
                } for d, a, p in zip(e["date"], e["actual"], e["pred"]))
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
                         "dir=%s  hit5=%.0f%%  n=%d  DM=%s",
                         tenor, model, point, band * 100, s["mae_bps"], s["rmse_bps"],
                         f"{s['dir_acc']:.0%}" if s["dir_acc"] is not None else "n/a",
                         s["hit_5bps"] * 100, s["n_obs"],
                         s.get("dm_pvalue") if s.get("dm_pvalue") is not None else "—")

        if skipped:
            log.info("skipped (too little history): %s", ", ".join(skipped))
        if not forecasts:
            raise RuntimeError("no tenor had enough history to forecast")

        if dry_run:
            log.info("DRY RUN — %d forecasts / %d metrics / %d predictions not written",
                     len(forecasts), len(metrics), len(preds))
            return {"forecasts": len(forecasts), "metrics": len(metrics),
                    "predictions": len(preds), "scored": 0, "dry_run": True}

        scored = score_past_forecasts(session, hist)
        nf = save_forecasts(session, forecasts)
        nm = save_metrics(session, metrics)
        np_ = save_predictions(session, preds)
        log.info("wrote %d new forecasts (%d total), %d new metrics (%d total), "
                 "%d new predictions (%d total), scored %d past forecasts",
                 nf, len(forecasts), nm, len(metrics), np_, len(preds), scored)
        return {"forecasts": len(forecasts), "metrics": len(metrics),
                "predictions": len(preds), "scored": scored, "dry_run": False}
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
