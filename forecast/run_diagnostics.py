"""
forecast/run_diagnostics.py — guide Phase 4 statistical testing.

THE ONLY FILE IN THIS REPO THAT IMPORTS statsmodels. It runs in GitHub Actions
against requirements-forecast.txt and writes results to research_diagnostics;
the API never imports it. Keeping inference here is what lets the weekly
production forecast (run_forecast.py) stay pure numpy and dependency-free.

What it answers:
  ADF + KPSS   are cutoff levels really non-stationary, and are changes
               stationary? Decides levels-vs-differences, and confirms that
               modelling the CHANGE (as run_forecast does) is the right call.
  Granger      do the auction-demand factors actually lead the cutoff, or do
               they only look correlated?
  OLS + HAC    Newey-West coefficient inference on the Phase-2 factor set —
               with the sign checks the guide demands.
  VIF          are the factors collinear enough to make those signs unstable?
  Ljung-Box    is there autocorrelation left in the residuals?

Usage:  python forecast/run_diagnostics.py [--dry-run]
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

from db import init_db, get_session, ResearchDiagnostic          # noqa: E402
from forecast.features import build as build_features, FEATURE_COLS  # noqa: E402
from forecast.run_forecast import load_history, MIN_SCORED, MIN_TRAIN  # noqa: E402
from forecast.policy import (load_history as policy_history,     # noqa: E402
                            usable as policy_usable, attach as policy_attach)

log = logging.getLogger("diagnostics")

GRANGER_LAGS = 4
HAC_LAGS = 4
ALPHA = 0.05

# Signs the guide says these factors MUST have if the specification is sound.
# A wrong sign is a specification problem, not a discovery.
EXPECTED_SIGN = {
    "d_cutoff_lag1":  +1,   # momentum carries forward
    "cover_lag1":     -1,   # heavy cover = strong demand = lower cutoff
    "d_cover_lag1":   -1,   # demand improving = cutoff eases
    "size_lag1":      +1,   # heavier supply = higher cutoff
    "level_gap_lag1": -1,   # above trailing mean = pull back down
}


def _rows(computed_date, tenor, test, subject, stat, p, lag, concl):
    return {"computed_date": computed_date, "tenor": tenor, "test": test,
            "subject": subject,
            "statistic": None if stat is None else round(float(stat), 4),
            "pvalue": None if p is None else round(float(p), 4),
            # cast: numpy ints/floats reach here from statsmodels and psycopg2
            # cannot adapt them ("can't adapt type 'numpy.int64'").
            "lag": None if lag is None else int(lag),
            "conclusion": concl[:120]}


def diagnose_tenor(tenor: str, f: pd.DataFrame, today) -> list[dict]:
    from statsmodels.tsa.stattools import adfuller, kpss, grangercausalitytests
    from statsmodels.stats.diagnostic import acorr_ljungbox
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    import statsmodels.api as sm

    out = []
    level = f["cutoff_yield_pct"].astype(float).to_numpy()
    change = f["d_cutoff"].astype(float).to_numpy()

    # ── 4.1 stationarity ──────────────────────────────────────────────────
    for name, series in (("cutoff_level", level), ("d_cutoff", change)):
        try:
            stat, p, *_ = adfuller(series, autolag="AIC")
            concl = ("rejects unit root — stationary" if p < ALPHA
                     else "cannot reject unit root — non-stationary")
            out.append(_rows(today, tenor, "adf", name, stat, p, None, concl))
        except Exception as exc:
            log.warning("ADF failed %s/%s: %s", tenor, name, exc)
        try:
            import warnings
            with warnings.catch_warnings():      # KPSS warns when p is clipped
                warnings.simplefilter("ignore")
                stat, p, *_ = kpss(series, regression="c", nlags="auto")
            concl = ("rejects stationarity" if p < ALPHA
                     else "consistent with stationarity")
            out.append(_rows(today, tenor, "kpss", name, stat, p, None, concl))
        except Exception as exc:
            log.warning("KPSS failed %s/%s: %s", tenor, name, exc)

    # ── 4.4 Granger causality: does each factor lead the cutoff change? ────
    for feat in FEATURE_COLS:
        try:
            data = f[["d_cutoff", feat]].astype(float).dropna()
            if len(data) < 4 * GRANGER_LAGS + 10:
                continue
            import warnings, contextlib, io
            # grangercausalitytests prints a full report per lag to stdout;
            # swallow it so the CI log stays readable.
            with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
                warnings.simplefilter("ignore")
                res = grangercausalitytests(data, maxlag=GRANGER_LAGS)
            best_lag, best_p = min(
                ((lag, r[0]["ssr_ftest"][1]) for lag, r in res.items()),
                key=lambda kv: kv[1])
            concl = (f"Granger-causes d_cutoff at lag {best_lag}" if best_p < ALPHA
                     else "no Granger causality detected")
            out.append(_rows(today, tenor, "granger", feat, None, best_p, best_lag, concl))
        except Exception as exc:
            log.warning("Granger failed %s/%s: %s", tenor, feat, exc)

    # ── 4.2/5.2 OLS with Newey-West, VIF, Ljung-Box ───────────────────────
    try:
        X = f[FEATURE_COLS].astype(float)
        y = f["d_cutoff"].astype(float)
        Xc = sm.add_constant(X)
        fit = sm.OLS(y, Xc).fit(cov_type="HAC", cov_kwds={"maxlags": HAC_LAGS})

        for feat in FEATURE_COLS:
            coef, p = float(fit.params[feat]), float(fit.pvalues[feat])
            want = EXPECTED_SIGN.get(feat)
            sign_ok = want is None or np.sign(coef) == want or coef == 0
            concl = ("significant" if p < ALPHA else "not significant")
            if not sign_ok:
                concl += "; SIGN WRONG vs theory"
            out.append(_rows(today, tenor, "ols_coef", feat, coef, p, None, concl))

        out.append(_rows(today, tenor, "ols_fit", "r_squared",
                         fit.rsquared, None, None,
                         f"adj R2={fit.rsquared_adj:.4f}, n={int(fit.nobs)}"))

        # VIF — needs the constant column present to be meaningful
        vals = Xc.to_numpy(dtype=float)
        for i, feat in enumerate(Xc.columns):
            if feat == "const":
                continue
            v = variance_inflation_factor(vals, i)
            concl = "collinear (VIF>5)" if v > 5 else "acceptable"
            out.append(_rows(today, tenor, "vif", feat, v, None, None, concl))

        lb = acorr_ljungbox(fit.resid, lags=[HAC_LAGS], return_df=True)
        stat = float(lb["lb_stat"].iloc[0]); p = float(lb["lb_pvalue"].iloc[0])
        concl = ("residual autocorrelation present" if p < ALPHA
                 else "no residual autocorrelation")
        out.append(_rows(today, tenor, "ljungbox", "ols_residuals", stat, p, HAC_LAGS, concl))
    except Exception as exc:
        log.warning("OLS diagnostics failed %s: %s", tenor, exc)

    return out


def cointegration(tenor: str, f: pd.DataFrame, today) -> list[dict]:
    """Engle-Granger between the cutoff level and the policy rate (guide 4.5).

    If they are cointegrated, the spread is stationary and an error-correction
    model is the theoretically right specification: cutoffs wander but get
    pulled back to the corridor. This runs ONLY on verified corridor history —
    see forecast/policy.py for why that guard is non-negotiable.
    """
    from statsmodels.tsa.stattools import coint, adfuller
    out = []
    d = f[["cutoff_yield_pct", "repo_now"]].dropna()
    if len(d) < 30 or d["repo_now"].nunique() < 3:
        return out
    try:
        stat, p, _ = coint(d["cutoff_yield_pct"].astype(float), d["repo_now"].astype(float))
        concl = ("cointegrated with policy rate — ECM is the right spec" if p < ALPHA
                 else "no cointegration detected — ECM not justified")
        out.append(_rows(today, tenor, "coint", "cutoff~repo", stat, p, None, concl))
    except Exception as exc:
        log.warning("coint failed %s: %s", tenor, exc)
    try:
        spread = (d["cutoff_yield_pct"] - d["repo_now"]).astype(float).to_numpy()
        stat, p, *_ = adfuller(spread, autolag="AIC")
        concl = ("spread is stationary — mean-reverting to the corridor" if p < ALPHA
                 else "spread not stationary")
        out.append(_rows(today, tenor, "adf", "spread_policy", stat, p, None, concl))
    except Exception as exc:
        log.warning("spread ADF failed %s: %s", tenor, exc)
    return out


def save(session, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        existing = session.query(ResearchDiagnostic).filter_by(
            computed_date=r["computed_date"], tenor=r["tenor"],
            test=r["test"], subject=r["subject"]).first()
        if existing:
            for k, v in r.items():
                setattr(existing, k, v)
        else:
            session.add(ResearchDiagnostic(**r))
            n += 1
    session.commit()
    return n


def run(dry_run: bool = False) -> dict:
    today = datetime.date.today()
    session = get_session()
    try:
        hist = load_history(session)
        pol = policy_history(session)
        ecm_ok, ecm_why = policy_usable(pol, hist["auction_date"].min())
        log.info("ECM gate: %s — %s", "OPEN" if ecm_ok else "BLOCKED", ecm_why)

        # Always record the gate's verdict, open or shut. A blocked ECM should
        # be visible on the page with the reason, not silently missing.
        rows = [_rows(today, "ALL", "policy_gate", "ecm", None, None, None,
                      ("OPEN — " if ecm_ok else "BLOCKED — ") + ecm_why)]
        tested = []
        for (tenor, _instrument), g in hist.groupby(["tenor_label", "security_type"], sort=True):
            f = build_features(g.sort_values("auction_date"))
            if len(f) - MIN_TRAIN < MIN_SCORED:
                continue
            r = diagnose_tenor(tenor, f, today)
            if ecm_ok:
                r.extend(cointegration(tenor, policy_attach(f, pol), today))
            rows.extend(r)
            tested.append(f"{tenor}({len(f)})")

        log.info("tested %d tenors: %s", len(tested), ", ".join(tested))
        for r in rows:
            if r["test"] in ("adf", "kpss", "granger"):
                log.info("  %-8s %-9s %-16s p=%s  %s", r["tenor"], r["test"],
                         r["subject"], r["pvalue"], r["conclusion"])

        if dry_run:
            log.info("DRY RUN — %d diagnostic rows not written", len(rows))
            return {"rows": len(rows), "dry_run": True}
        n = save(session, rows)
        log.info("wrote %d new diagnostic rows (%d total)", n, len(rows))
        return {"rows": len(rows), "new": n, "dry_run": False}
    finally:
        session.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    dry_run = "--dry-run" in sys.argv
    if not dry_run:
        if not os.environ.get("DATABASE_URL"):
            log.error("DATABASE_URL is not set — refusing to run against local SQLite")
            return 1
        init_db()
    log.info("done: %s", run(dry_run=dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
