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


def build_anchor(hist: pd.DataFrame, tenor: str = "364D") -> pd.DataFrame:
    """The market's freshest observable rate: the weekly bill cutoff series.

    Bills auction every week; bonds auction roughly monthly. Between two bond
    auctions the bill curve prints ~4 more times, so by the time a bond is bid
    the market has moved in a way the previous bond cutoff cannot know about.
    That move is the information a naive forecast throws away.
    """
    a = (hist[hist["tenor_label"] == tenor][["auction_date", "cutoff_yield_pct"]]
         .dropna().sort_values("auction_date")
         .rename(columns={"cutoff_yield_pct": "anchor_yield"}))
    return a.drop_duplicates(subset=["auction_date"], keep="last").reset_index(drop=True)


def add_curve_anchor(f: pd.DataFrame, anchor: pd.DataFrame) -> pd.DataFrame:
    """Attach the anchor rate KNOWN BEFORE each auction, and its move since the
    previous auction of this tenor.

    `allow_exact_matches=False` is the whole safety argument: a bond and a bill
    can print on the same day, and that day's bill result is not knowable when
    the bond is bid. Matching strictly earlier keeps the feature honest.

    `anchor_now` is not lagged and does not need to be — it is built only from
    auctions strictly before t, so it is in the bidder's information set.
    `d_anchor` is how far the anchor moved between this auction's information
    set and the previous one's.
    """
    if anchor.empty or f.empty:
        f = f.copy()
        f["anchor_now"] = np.nan
        f["d_anchor"] = np.nan
        return f

    merged = pd.merge_asof(
        f.sort_values("auction_date"),
        anchor.sort_values("auction_date"),
        on="auction_date", direction="backward", allow_exact_matches=False,
    )
    merged = merged.rename(columns={"anchor_yield": "anchor_now"})
    merged["d_anchor"] = merged["anchor_now"].diff()
    return merged


def beta_no_intercept(x: np.ndarray, y: np.ndarray) -> float:
    """Slope through the origin: b = sum(xy)/sum(xx). 0.0 when unidentified."""
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 5:
        return 0.0
    denom = float(np.dot(x, x))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(x, y) / denom)


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
