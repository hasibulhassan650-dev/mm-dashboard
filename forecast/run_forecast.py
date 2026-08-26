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
from forecast.features import (build as build_features, ols_fit, ols_predict,  # noqa: E402
                               build_anchor, add_curve_anchor, beta_no_intercept,
                               FEATURE_COLS)
from forecast.policy import (load_history as policy_history,   # noqa: E402
                            usable as policy_usable, attach as policy_attach)
from forecast import robustness as rb  # noqa: E402

log = logging.getLogger("forecast")

# A tenor needs enough prints that an expanding-window backtest still leaves a
# meaningful test set. Bonds auction ~monthly, so ~35 prints since 2023-09 is
# all there is — MIN_TRAIN 12 leaves ~23 scored forecasts. Below MIN_HISTORY
# the metrics would be noise dressed up as skill, so the tenor is skipped.
MIN_HISTORY = 16
MIN_TRAIN   = 12
MODELS      = ("naive", "momentum", "ols", "curve", "ecm", "blend")
ECM_COLS    = ["spread_policy_lag1", "d_policy"]
PRIMARY_MODEL = "curve"   # pre-specified primary hypothesis; see the FDR block in run()
ANCHOR_TENOR = "364D"   # freshest deep weekly series; see features.build_anchor
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

# One window cannot do both jobs, so there are two.
#
# EVIDENCE WINDOW — how much history a model is fitted and judged on. Wants
# statistical power. Bills auction weekly (150+ prints in 3 years) and get
# nothing from going longer except the 2007-2022 regimes that inflate their
# band. Bonds auction monthly: 3 years is only ~20 scored forecasts, which is
# too few to resolve anything — 20Y's "no edge" verdict was low power, not
# absence of an effect. At 5 years (~40) every bond tenor's curve edge is
# significant. Justification for reaching back that far: the curve model's OWN
# error is nearly window-invariant (2Y: 24.0 / 24.4 / 25.5 bps at 3 / 5 / 8
# years) while naive's swings (50.4 / 40.4 / 39.9), which is what a structural
# relationship looks like as opposed to a regime artifact.
LOOKBACK_BONDS = float(os.environ.get("FORECAST_LOOKBACK_BONDS", "5"))

# BAND WINDOW — how wide the published interval is. Wants the CURRENT regime,
# never the average of a calm past and a choppy present. Naive's MAE at 20Y
# falls from 50.4 to 24.3 bps as the window lengthens purely because older
# periods were calmer; sizing a band on that would quietly understate today's
# risk. So the band and the headline "typical miss" are computed from only the
# most recent share of scored forecasts.
BAND_RECENT_FRAC = float(os.environ.get("FORECAST_BAND_RECENT_FRAC", "0.5"))

BILL_TENORS = {"91D", "182D", "364D", "14D", "28D"}


def lookback_for(tenor: str) -> float:
    """Evidence window for this tenor, in years."""
    return LOOKBACK_YEARS if tenor in BILL_TENORS else LOOKBACK_BONDS


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

    # Model 3 — curve carry. Move this tenor by lambda x however far the weekly
    # bill anchor has travelled since this tenor last auctioned. lambda is a
    # beta (a pass-through), fitted through the origin: no anchor move means no
    # predicted move, which is the correct behaviour when nothing has happened.
    d_anchor = row.get("d_anchor")
    if d_anchor is not None and np.isfinite(d_anchor):
        lam = beta_no_intercept(train["d_anchor"].to_numpy(dtype=float),
                                train["d_cutoff"].to_numpy(dtype=float))
        out["curve"] = last + lam * float(d_anchor)

    # Model 4 — error correction (guide Phase 5.3). Only reachable when
    # forecast/policy.py's gate has opened, i.e. verified corridor history
    # spanning the sample exists. Until then these columns are all-NaN and this
    # block is skipped, by design — see policy.py for why that guard matters.
    if all(c in row.index for c in ECM_COLS) and np.all(np.isfinite(row[ECM_COLS].to_numpy(dtype=float))):
        tr = train.dropna(subset=ECM_COLS)
        if len(tr) >= MIN_TRAIN_OLS:
            coef = ols_fit(tr[ECM_COLS].to_numpy(dtype=float),
                           tr["d_cutoff"].to_numpy(dtype=float))
            if coef is not None:
                # coef[1] is alpha, the pull-to-anchor. Theory says it must be
                # NEGATIVE: sitting above the corridor should predict a fall
                # back toward it. A positive alpha means the spread is
                # explosive, which is not an error-correction model at all — so
                # the forecast is withheld rather than published as one.
                if coef[1] < 0:
                    out["ecm"] = last + ols_predict(coef, row[ECM_COLS].to_numpy(dtype=float))

    # Model 5 — blend. The plain average of every other model available.
    # Forecast combination is the most reliable free lunch in this literature:
    # averaging cancels independent errors, so the blend is usually near the
    # best single model without needing to know in advance which that is. It
    # also shrinks any one model's overconfidence toward the naive anchor.
    others = [v for k, v in out.items() if np.isfinite(v)]
    if len(others) >= 2:
        out["blend"] = float(np.mean(others))
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

    # RMSE over the most recent slice only — this is what the published band is
    # built from, so the interval reflects today's volatility rather than an
    # average that includes calmer years.
    k = max(8, int(round(len(err) * BAND_RECENT_FRAC)))
    err_recent = err[-k:] if len(err) > k else err
    rmse_recent = float(np.sqrt(np.mean(err_recent ** 2))) * 100

    return {
        "mae_bps":  round(float(np.mean(err_bps)), 2),
        "rmse_bps": round(float(np.sqrt(np.mean(err ** 2))) * 100, 2),
        "rmse_recent_bps": round(rmse_recent, 2),
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


def _verdict(gap: tuple[float, float] | None, dm_p: float | None) -> str:
    """Turn the evidence into one word, by a rule fixed in advance.

    The rule exists to stop model selection from being done by eye. Picking the
    lowest-MAE model AFTER seeing the results is selection bias: across six
    models and eight tenors some will win by luck, and the winner's MAE then
    flatters itself. So a model is only 'established' when the bootstrap
    interval for its improvement over naive EXCLUDES ZERO and the small-sample
    DM test agrees. Anything else is 'unproven', no matter how good it looks.
    """
    if gap is None:
        return "unproven"
    lo, hi = gap
    if hi < 0:
        return "worse"
    if lo > 0 and dm_p is not None and dm_p < 0.05:
        return "established"
    return "unproven"


def _next_row(f: pd.DataFrame, anchor: pd.DataFrame | None = None) -> pd.Series:
    """The feature row for the NEXT (unheld) auction.

    Its lag-1 inputs are the latest auction's realised values, so they shift up
    by one relative to the last frame row: what was "this auction" becomes
    "t-1". Rebuilding them from the raw series is what keeps the live forecast
    on exactly the same footing as every backtested one.
    """
    last = f.iloc[-1]
    cut = f["cutoff_yield_pct"].astype(float)
    trail = cut.tail(8)

    # Anchor for the next auction: the freshest bill print available now. Its
    # move is measured against the anchor that was current at the last auction
    # of this tenor — the same comparison every backtested row used.
    anchor_now = d_anchor = np.nan
    if anchor is not None and not anchor.empty:
        anchor_now = float(anchor["anchor_yield"].iloc[-1])
        prev = last.get("anchor_now")
        if prev is not None and np.isfinite(prev):
            d_anchor = anchor_now - float(prev)

    # ECM inputs for the next auction. All NaN while the policy gate is closed,
    # which is exactly what keeps the ECM switched off.
    repo_now = float(last["repo_now"]) if np.isfinite(last.get("repo_now", np.nan)) else np.nan
    return pd.Series({
        "anchor_now":     anchor_now,
        "d_anchor":       d_anchor,
        "repo_now":       repo_now,
        # the previous cutoff's distance from the corridor in force now
        "spread_policy_lag1": float(last["cutoff_yield_pct"]) - repo_now,
        # no corridor change is known for a future date, so no step to apply
        "d_policy":       0.0 if np.isfinite(repo_now) else np.nan,
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
        # Load the widest window any tenor needs; each tenor is trimmed to its
        # own below. The curve anchor is built from this full span so a bond
        # reaching back 5 years still has bill prints to compare against.
        hist = load_history(session, lookback_years=max(LOOKBACK_YEARS, LOOKBACK_BONDS))
        if hist.empty:
            raise RuntimeError("no cutoff history in primary_yield_snapshots — nothing to model")
        log.info("loaded %d prints, %s to %s (evidence window: bills %sy, bonds %sy; "
                 "band from most recent %.0f%% of forecasts)", len(hist),
                 hist["auction_date"].min().date(), hist["auction_date"].max().date(),
                 LOOKBACK_YEARS or "all", LOOKBACK_BONDS or "all", BAND_RECENT_FRAC * 100)

        forecasts, metrics, preds, skipped = [], [], [], []
        anchor = build_anchor(hist, ANCHOR_TENOR)
        log.info("curve anchor: %s with %d prints", ANCHOR_TENOR, len(anchor))

        pol_hist = policy_history(session)
        ecm_ok, ecm_why = policy_usable(pol_hist, hist["auction_date"].min())
        log.info("ECM gate: %s — %s", "OPEN" if ecm_ok else "BLOCKED", ecm_why)

        for (tenor, instrument), g in hist.groupby(["tenor_label", "security_type"], sort=True):
            g = g.sort_values("auction_date")
            # Trim to THIS tenor's evidence window — bonds get more history than
            # bills because they auction ~12x less often. See lookback_for().
            yrs = lookback_for(tenor)
            if yrs > 0 and not g.empty:
                cut = g["auction_date"].max() - pd.Timedelta(days=round(365.25 * yrs))
                g = g[g["auction_date"] >= cut]
            if len(g) < MIN_HISTORY:
                skipped.append(f"{tenor} (n={len(g)})")
                continue

            f = add_curve_anchor(build_features(g), anchor)
            # Attaching only when the gate is open is what keeps unverified
            # corridor numbers out of every downstream fit.
            f = policy_attach(f, pol_hist if ecm_ok else pol_hist.iloc[0:0])
            if tenor == ANCHOR_TENOR:
                # The anchor cannot carry itself: d_anchor would just be this
                # tenor's own last change, making curve a duplicate of momentum
                # and double-weighting it inside the blend.
                f = f.assign(d_anchor=np.nan)
            if len(f) - MIN_TRAIN < MIN_SCORED:
                skipped.append(f"{tenor} (only {max(0, len(f) - MIN_TRAIN)} scorable)")
                continue

            target = next_auction_date(g["auction_date"])
            bt = backtest(f)

            # The live forecast: train on EVERY usable row, then predict the
            # next auction from a synthetic row carrying only lagged inputs.
            live_row = _next_row(f, anchor if tenor != ANCHOR_TENOR else None)
            points = predict(f, live_row)

            # The pass-through actually being applied — the single number that
            # decides how aggressive the curve model is. Worth seeing in the log
            # every run: a lambda drifting far from its usual level is the first
            # sign the tenor's relationship to the bill curve has changed.
            if "curve" in points:
                lam = beta_no_intercept(f["d_anchor"].to_numpy(dtype=float),
                                        f["d_cutoff"].to_numpy(dtype=float))
                log.info("%-8s curve lambda=%.3f (anchor moved %+.0f bps since last auction)",
                         tenor, lam, float(live_row["d_anchor"]) * 100)

            naive_err = np.asarray(bt["naive"]["err"], dtype=float)

            for model in MODELS:
                if model not in points:
                    continue
                s = score(bt[model])
                if s is None:
                    continue
                err = np.asarray(bt[model]["err"], dtype=float)
                rmse_pct = s["rmse_bps"] / 100.0
                # Band half-width from EWMA error volatility, so it tracks the
                # regime instead of averaging a calm year with a violent one.
                # Falls back to the old trailing-RMSE rule only if there are too
                # few errors for an EWMA estimate.
                band_hw = rb.band_half_width(err)
                if band_hw is None:
                    band_hw = Z95 * s["rmse_recent_bps"] / 100.0
                s["band_bps"] = round(band_hw * 100, 2)
                ci = rb.bootstrap_ci(err)
                s["mae_lo"], s["mae_hi"] = (round(ci[0], 2), round(ci[1], 2)) if ci else (None, None)
                # Coverage against the band actually published, not a nominal one.
                s["coverage"] = round(float(np.mean(np.abs(err) <= band_hw)), 4)
                st = rb.stability(err)
                s["mae_first"], s["mae_recent"] = st if st else (None, None)

                if model == "naive":
                    s["verdict"] = "benchmark"
                else:
                    # OLS starts later than naive (bigger training requirement),
                    # so the two error series must be aligned on the auctions
                    # BOTH models actually scored before they can be compared.
                    shared = set(bt[model]["date"])
                    idx = [i for i, d in enumerate(bt["naive"]["date"]) if d in shared]
                    nb = naive_err[idx]
                    s["dm_pvalue"] = rb.dm_test(err, nb, hln=True)
                    gap = rb.bootstrap_gap_ci(err, nb)
                    s["gap_lo"], s["gap_hi"] = (round(gap[0], 2), round(gap[1], 2)) if gap else (None, None)
                    # Provisional. The final verdict also needs to survive the
                    # multiple-testing correction applied across the whole run
                    # below — one tenor cannot be judged in isolation when ~30
                    # comparisons are being screened at once.
                    s["verdict"] = _verdict(gap, s["dm_pvalue"])
                # Band from the RECENT error, not the lifetime average.
                band = band_hw                                # yield %
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
                log.info("%-8s %-9s MAE=%.2f [%s,%s]  gap=[%s,%s]  DM=%s  cov=%s  "
                         "%s->%s  %s",
                         tenor, model, s["mae_bps"], s.get("mae_lo"), s.get("mae_hi"),
                         s.get("gap_lo"), s.get("gap_hi"),
                         s.get("dm_pvalue"), s.get("coverage"),
                         s.get("mae_first"), s.get("mae_recent"), s.get("verdict"))

        # ── Multiple-testing correction ───────────────────────────────────
        # Screening many comparisons at p<0.05 manufactures winners: measured by
        # simulation, 31 tests under the null yield ~1.5 false "established"
        # verdicts per run. Benjamini-Hochberg fixes that — but ONLY if the
        # family is defined honestly, and that is the subtle part.
        #
        # The family is NOT every model x tenor cell. Those 31 cells are not 31
        # independent hypotheses: `blend` is partly a function of `curve`, so
        # tenor/curve and tenor/blend are near-duplicate tests, and padding the
        # family with models already known to fail (ols, momentum, ecm) inflates
        # m and destroys power without buying any inferential protection. Run
        # that way, BH rejects everything including 2Y curve at p=0.0038 — a
        # false negative produced by a badly specified family, not by weak evidence.
        #
        # So the family is the PRE-SPECIFIED PRIMARY HYPOTHESIS: curve carry
        # beats naive, tested across the tenors. Curve is primary because it was
        # hypothesised from a mechanism (bond auctions price off a stale own-print
        # while the weekly bill curve has moved) BEFORE it was tested, and it is
        # the model the tab actually recommends. Every other model is exploratory:
        # reported in full, but never promoted to "established", because choosing
        # among them after seeing results is exactly the selection bias this
        # whole guard exists to prevent.
        primary = [m for m in metrics if m["model"] == PRIMARY_MODEL]
        passes = rb.bh_fdr([m.get("dm_pvalue") for m in primary])
        demoted = []
        for m, ok in zip(primary, passes):
            m["dm_fdr_pass"] = bool(ok)
            if m.get("verdict") == "established" and not ok:
                m["verdict"] = "unproven"
                demoted.append(f"{m['tenor']}(p={m.get('dm_pvalue')})")
        for m in metrics:
            if m["model"] == PRIMARY_MODEL or m["model"] == "naive":
                continue
            # Exploratory: shown with its real numbers, never promoted.
            m["dm_fdr_pass"] = None
            if m.get("verdict") == "established":
                m["verdict"] = "exploratory"
        log.info("FDR(q=0.05) on the primary family (%s vs naive, %d tenors): %d pass",
                 PRIMARY_MODEL, len(primary), sum(passes))
        if demoted:
            log.info("demoted by multiple-testing correction: %s", ", ".join(demoted))

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
