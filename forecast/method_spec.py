"""
forecast/method_spec.py - the forecasting method's constants, read from the code.

WHY THIS FILE EXISTS. The /research page explains how next-auction cutoffs are
forecast. Every number in that explanation (minimum prints, lookback windows,
EWMA lambda, bootstrap draws, thresholds ...) must be the number the model
actually uses, and the owner must never have to keep the two in step by hand.
So nothing here is retyped: every value is imported from the module that uses it
(or read from a function's default argument), and the result is written to
frontend/lib/forecastMethod.json, which the page imports.

    python -m forecast.method_spec            print the spec
    python -m forecast.method_spec --write    regenerate frontend/lib/forecastMethod.json

tests/test_forecast_method.py fails if the committed JSON no longer equals
spec(), so changing a constant without regenerating breaks the build.
"""
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forecast import run_forecast as rf       # noqa: E402
from forecast import robustness as rb         # noqa: E402
from forecast import features as ft           # noqa: E402
from forecast import policy as pol            # noqa: E402
from forecast import run_diagnostics as dg    # noqa: E402
from forecast import economic_value as ev     # noqa: E402

OUT = ROOT / "frontend" / "lib" / "forecastMethod.json"


def _default(fn, arg):
    """A function's default argument value - the constant it really uses."""
    return inspect.signature(fn).parameters[arg].default


def spec() -> dict:
    return {
        "models": list(rf.MODELS),
        "primary_model": rf.PRIMARY_MODEL,
        "anchor_tenor": rf.ANCHOR_TENOR,
        "bill_tenors": sorted(rf.BILL_TENORS),
        "factors": list(ft.FEATURE_COLS),
        "ecm_factors": list(rf.ECM_COLS),
        "trailing_window": ft.TRAIL,
        "lookback_years": {"bills": rf.LOOKBACK_YEARS, "bonds": rf.LOOKBACK_BONDS},
        "min_history": rf.MIN_HISTORY,
        "min_train": rf.MIN_TRAIN,
        "min_train_regression": rf.MIN_TRAIN_OLS,
        "min_scored": rf.MIN_SCORED,
        "next_date_gap_window": rf.GAP_WINDOW,
        "hit_bps": rf.HIT_BPS,
        "band": {
            "z": _default(rb.band_half_width, "z"),
            "fallback_z": rf.Z95,
            "ewma_lambda": rb.EWMA_LAMBDA,
            "fallback_recent_fraction": rf.BAND_RECENT_FRAC,
        },
        "bootstrap": {
            "draws": rb.N_BOOT,
            "block_fraction": rb.BLOCK_FRAC,
            "seed": rb.RNG_SEED,
            "ci_level": round(1 - _default(rb.bootstrap_gap_ci, "alpha"), 6),
        },
        "verdict": {
            "dm_alpha": rf.DM_ALPHA,
            "dm_hln_correction": _default(rb.dm_test, "hln"),
            "fdr_q": _default(rb.bh_fdr, "q"),
        },
        "ecm_gate": {
            "min_verified_points": pol.MIN_VERIFIED_POINTS,
            "min_distinct_repo": pol.MIN_DISTINCT_REPO,
            "min_covered_years": pol.MIN_COVERED_YEARS,
        },
        "diagnostics": {
            "alpha": dg.ALPHA,
            "granger_lags": dg.GRANGER_LAGS,
            "hac_lags": dg.HAC_LAGS,
        },
        "economic_value": {"target_fill": ev.TARGET_FILL},
    }


def main() -> int:
    text = json.dumps(spec(), indent=2) + "\n"
    if "--write" in sys.argv:
        OUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {OUT}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
