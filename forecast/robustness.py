"""
forecast/robustness.py — does the edge survive honest scrutiny?

A lower MAE in a backtest is a claim, not a fact. This module attacks that claim
four ways, all in pure numpy so the weekly forecast keeps no stats dependency.

1. BOOTSTRAP CONFIDENCE INTERVAL on MAE, and on the model-minus-naive gap.
   A point estimate from 20 bond auctions carries enormous uncertainty. Uses a
   MOVING BLOCK bootstrap (not i.i.d. resampling) because auction errors are
   serially correlated — resampling single observations would pretend the
   sample holds more independent information than it does, and produce
   intervals that are far too narrow.

2. HARVEY-LEYBOURNE-NEWBOLD correction to Diebold-Mariano.
   The plain DM statistic is badly over-sized in small samples: with n=20 it
   rejects the null far more often than 5% of the time when the null is true.
   HLN rescales it and reads off a t-distribution with n-1 df instead of a
   normal. This is the difference between an edge that is real and one that is
   an artefact of a short sample.

3. BAND COVERAGE. The published interval is +/-1.96 x RMSE, which is only a 95%
   interval if the errors are normal. Rate-change errors are famously fat-tailed.
   So measure it: what share of actual prints really landed inside the band?
   If coverage is 85% the band is being advertised as something it is not.

4. SUB-PERIOD STABILITY. Split the backtest in half and score each. A model that
   wins overall but only worked in the first half has not found a durable
   relationship — it found a regime that has since ended.
"""
import numpy as np

BLOCK_FRAC = 0.15      # block length as a share of n; keeps local dependence
N_BOOT = 2000
RNG_SEED = 7           # fixed so a published interval is reproducible


def _blocks(n: int) -> int:
    return max(2, min(n, int(round(n * BLOCK_FRAC))))


def _moving_block_resample(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One moving-block bootstrap replicate of a serially correlated series."""
    n = len(x)
    L = _blocks(n)
    k = int(np.ceil(n / L))
    starts = rng.integers(0, n - L + 1, size=k)
    return np.concatenate([x[s:s + L] for s in starts])[:n]


def bootstrap_ci(errors: np.ndarray, stat=lambda e: np.mean(np.abs(e)) * 100,
                 n_boot: int = N_BOOT, alpha: float = 0.05) -> tuple[float, float] | None:
    """Percentile CI for a statistic of the error series. None if too short."""
    e = np.asarray(errors, dtype=float)
    e = e[np.isfinite(e)]
    if len(e) < 8:
        return None
    rng = np.random.default_rng(RNG_SEED)
    vals = np.empty(n_boot)
    for i in range(n_boot):
        vals[i] = stat(_moving_block_resample(e, rng))
    lo, hi = np.percentile(vals, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def bootstrap_gap_ci(err_model: np.ndarray, err_naive: np.ndarray,
                     n_boot: int = N_BOOT, alpha: float = 0.05) -> tuple[float, float] | None:
    """CI for (naive MAE - model MAE) in bps. Positive means the model is better.

    The two error series are resampled with the SAME blocks so the pairing
    survives — the models are compared on identical auctions, which is the whole
    point of a paired comparison.
    """
    a = np.asarray(err_model, dtype=float)
    b = np.asarray(err_naive, dtype=float)
    if len(a) != len(b) or len(a) < 8:
        return None
    n = len(a)
    L = _blocks(n)
    k = int(np.ceil(n / L))
    rng = np.random.default_rng(RNG_SEED)
    vals = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n - L + 1, size=k)
        idx = np.concatenate([np.arange(s, s + L) for s in starts])[:n]
        vals[i] = (np.mean(np.abs(b[idx])) - np.mean(np.abs(a[idx]))) * 100
    lo, hi = np.percentile(vals, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def _normal_cdf(z: float) -> float:
    from math import erf, sqrt
    return 0.5 * (1 + erf(z / sqrt(2)))


def _t_sf(t: float, df: int) -> float:
    """Two-sided t tail. Uses the incomplete beta via a continued fraction so
    this stays dependency-free (scipy is not available to the weekly job)."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return _betainc(df / 2.0, 0.5, x)


def _betainc(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a,b), Lentz continued fraction."""
    from math import lgamma, exp, log
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = lgamma(a) + lgamma(b) - lgamma(a + b)
    front = exp(log(x) * a + log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + num / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= c * d
        if abs(1.0 - c * d) < 1e-10:
            break
    return front * (f - 1.0)


def dm_test(err_model: np.ndarray, err_naive: np.ndarray, hln: bool = True) -> float | None:
    """Diebold-Mariano p-value on squared-error loss, model vs naive.

    With hln=True (default) applies the Harvey-Leybourne-Newbold small-sample
    correction and uses t(n-1). On ~20 bond auctions this is the difference
    between an honest p-value and an over-confident one.
    """
    a = np.asarray(err_model, dtype=float)
    b = np.asarray(err_naive, dtype=float)
    if len(a) != len(b) or len(a) < 10:
        return None
    d = b ** 2 - a ** 2
    n = len(d)
    dbar = float(np.mean(d))
    dc = d - dbar
    h = 1                                   # one-step-ahead forecasts
    lag = int(np.floor(4 * (n / 100) ** (2 / 9))) or 1
    gamma0 = float(np.dot(dc, dc) / n)
    lrv = gamma0
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * float(np.dot(dc[k:], dc[:-k]) / n)
    if lrv <= 0:
        return None
    dm = dbar / np.sqrt(lrv / n)
    if not hln:
        return round(2 * (1 - _normal_cdf(abs(dm))), 4)
    # HLN: shrink the statistic, then read off t with n-1 df
    adj = np.sqrt(max((n + 1 - 2 * h + h * (h - 1) / n) / n, 1e-9))
    return round(float(_t_sf(abs(dm * adj), n - 1)), 4)


def coverage(errors: np.ndarray, rmse_pct: float, z: float = 1.96) -> float | None:
    """Share of actual prints that fell inside the published +/-z*RMSE band.

    Should be ~0.95. Materially below means the band is too narrow for the
    real error distribution and is under-selling the risk.
    """
    e = np.asarray(errors, dtype=float)
    e = e[np.isfinite(e)]
    if len(e) < 8 or not np.isfinite(rmse_pct) or rmse_pct <= 0:
        return None
    return round(float(np.mean(np.abs(e) <= z * rmse_pct)), 4)


def stability(errors: np.ndarray) -> tuple[float, float] | None:
    """(first-half MAE, second-half MAE) in bps — did the edge persist?"""
    e = np.asarray(errors, dtype=float)
    e = e[np.isfinite(e)]
    if len(e) < 16:
        return None
    half = len(e) // 2
    return (round(float(np.mean(np.abs(e[:half]))) * 100, 2),
            round(float(np.mean(np.abs(e[half:]))) * 100, 2))
