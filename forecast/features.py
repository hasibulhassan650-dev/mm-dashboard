"""
forecast/features.py — per-auction factor construction (guide Phase 2).

GOLDEN RULE, enforced here: every feature on row t must be knowable BEFORE
bids for auction t are submitted. Anything measured at t (this auction's own
cutoff, offered, accepted) may only enter as a lag. That rule is the whole
reason this file exists as a separate, testable layer — lookahead is the #1
source of fake results in rate forecasting.

WHY THESE FACTORS AND NOT THE GUIDE'S FULL LIST
The guide's Tier-1 factors (call money spread, policy-rate change, excess
liquidity) cannot be built today: call_money_rates and omo_transactions only
start 2026-03, and policy_rate_snapshots holds a single row — there is no
policy time series to difference or cointegrate against. Fitting on them would
mean ~22 usable auctions and a fabricated anchor. So the factor set here is
restricted to what primary_yield_snapshots genuinely supports over 3 years:
auction-demand dynamics (Tier 2) plus own-history mean reversion.

`announced_amt` is NOT in the database, so the guide's bid-to-cover
(offered / announced) is not reproducible. We use offered / accepted, which is
the cover ratio on what BB actually took — a demand-pressure measure, but NOT
the same statistic. It is named cover_lag1 rather than btc to keep that honest.
"""
import numpy as np
import pandas as pd

# Trailing window for the mean-reversion and size factors. 8 auctions ~ two
# months of bills, which tracks the level without smoothing away the signal.
TRAIL = 8

FEATURE_COLS = ["d_cutoff_lag1", "cover_lag1", "d_cover_lag1", "size_lag1", "level_gap_lag1"]

FEATURE_DOC = {
    "d_cutoff_lag1":  "momentum — last auction's change in cutoff (pp)",
    "cover_lag1":     "cover ratio at t-1 = offered / accepted (demand pressure)",
    "d_cover_lag1":   "change in cover ratio at t-1 (demand turning)",
    "size_lag1":      "accepted at t-1 / trailing-8 mean accepted (supply pressure)",
    "level_gap_lag1": "cutoff at t-1 minus its trailing-8 mean (pp) — mean-reversion pull",
}


def build(g: pd.DataFrame) -> pd.DataFrame:
    """Feature frame for ONE tenor. `g` must be sorted ascending by auction_date.

    Returns rows with the target `d_cutoff` (change at t) and features that are
    all strictly lagged. Rows without a full feature set are dropped.
    """
    required = {"auction_date", "cutoff_yield_pct", "offered_bdt_crore", "accepted_bdt_crore"}
    missing = required - set(g.columns)
    if missing:
        # Fail loudly: a silently absent amount column would quietly turn every
        # demand factor into NaN and drop the whole tenor from the model.
        raise KeyError(f"features.build missing required columns: {sorted(missing)}")

    d = g.sort_values("auction_date").reset_index(drop=True).copy()
    cut = d["cutoff_yield_pct"].astype(float)

    # Target: change in cutoff at t. Levels are non-stationary; the change is
    # what the guide models and what the desk actually bids around.
    d["d_cutoff"] = cut.diff()

    # --- demand / supply, all shifted so row t only sees auction t-1 ---
    offered = pd.to_numeric(d["offered_bdt_crore"], errors="coerce")
    accepted = pd.to_numeric(d["accepted_bdt_crore"], errors="coerce")
    # Guard: accepted can be 0 or missing on a cancelled/devolved line.
    cover = offered / accepted.where(accepted > 0)

    d["cover_lag1"] = cover.shift(1)
    d["d_cover_lag1"] = cover.diff().shift(1)
    d["size_lag1"] = (accepted / accepted.rolling(TRAIL, min_periods=3).mean()).shift(1)

    # --- own history ---
    d["d_cutoff_lag1"] = d["d_cutoff"].shift(1)
    d["level_gap_lag1"] = (cut - cut.rolling(TRAIL, min_periods=3).mean()).shift(1)

    d["cutoff_lag1"] = cut.shift(1)      # the naive forecast, kept on every row

    # Contemporaneous values — realised AT t, so they are NOT features and must
    # never be fed to a model for auction t. They are carried only so the live
    # forecast can build the next auction's lag-1 inputs (see _next_row).
    d["cover_now"] = cover
    d["size_now"] = accepted / accepted.rolling(TRAIL, min_periods=3).mean()

    keep = (["auction_date", "cutoff_yield_pct", "cutoff_lag1", "d_cutoff"]
            + FEATURE_COLS + ["cover_now", "size_now"])
    out = d[keep].replace([np.inf, -np.inf], np.nan).dropna()
    return out.reset_index(drop=True)


def ols_fit(X: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    """Plain OLS coefficients with an intercept, via numpy least squares.

    Deliberately numpy and not statsmodels: the backtest refits this thousands
    of times, and keeping the FIT dependency-free means the weekly production
    forecast never needs the stats stack. statsmodels is used only for
    INFERENCE (HAC errors, diagnostics) in run_diagnostics.py.
    """
    if len(y) < X.shape[1] + 5:          # not enough rows to identify the fit
        return None
    A = np.column_stack([np.ones(len(X)), X])
    try:
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return beta if np.all(np.isfinite(beta)) else None


def ols_predict(beta: np.ndarray, x_row: np.ndarray) -> float:
    return float(beta[0] + np.dot(beta[1:], x_row))
