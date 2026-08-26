"""
forecast/economic_value.py — what a forecast edge is actually worth, in money.

MAE in basis points does not tell a treasury desk anything it can act on. This
turns the same out-of-sample errors into a bidding simulation and prices them.

AUCTION MECHANICS ASSUMED (multiple-price / "American" auction, which is how BB
runs primary G-sec auctions): BB fills competitive bids from the lowest yield
upward until it has raised what it needs. The highest accepted yield is the
cutoff. So a bidder who asks for a yield AT OR BELOW the cutoff is filled and
earns THEIR OWN bid yield; one who asks above it is left out entirely.

That creates the real tension a desk lives with:
  bid too high  -> you miss the auction and deploy nothing
  bid too low   -> you are filled, but you left yield on the table

A better forecast is worth money because it lets you bid closer to the cutoff
without missing. Perfect foresight would bid exactly at the cutoff every time.

HOW THIS AVOIDS THE USUAL OVER-CLAIM. The obvious way to score this needs an
assumption about what a missed auction earns instead (SDF? call money? nothing?)
and the answer swings wildly with that guess. So it is sidestepped: each model
is bid with a shading margin delta below its own forecast, delta is swept, and
the models are compared AT THE SAME FILL RATE. Equal fills means equal deployed
notional, so no reinvestment assumption is needed and the comparison is clean.

WHAT THIS DELIBERATELY IGNORES (state it, do not bury it):
  - no partial fills; a bid is all-or-nothing
  - no competitor reaction, and no reflexivity: bidding differently would at the
    margin move the cutoff being forecast
  - no bid-shading behaviour by other participants
  - a fixed notional every auction, ignoring the desk's actual funding needs
  - uniform-price auctions would change the arithmetic (everyone earns the
    cutoff), making forecast accuracy worth LESS on the fill margin
"""
import numpy as np

TARGET_FILL = 0.90     # the operating point the desk is compared at
SHADE_GRID = np.arange(0.0, 1.51, 0.005)   # 0 to 150 bps, in 0.5bp steps


def frontier(forecast: np.ndarray, actual: np.ndarray) -> list[dict]:
    """Fill rate vs captured yield across bid-shading margins.

    Assumption-free: it is just a restatement of the same OOS errors in bidding
    terms. Returns one row per shade level.
    """
    f = np.asarray(forecast, dtype=float)
    a = np.asarray(actual, dtype=float)
    m = np.isfinite(f) & np.isfinite(a)
    f, a = f[m], a[m]
    if len(f) < 8:
        return []
    out = []
    for d in SHADE_GRID:
        bid = f - d
        filled = bid <= a
        if not filled.any():
            continue
        out.append({
            "shade_bps": round(float(d) * 100, 1),
            "fill_rate": round(float(filled.mean()), 4),
            # Yield captured per auction ATTEMPTED, counting a miss as zero
            # deployed. Only comparable between models at equal fill rate.
            "captured_on_filled_bps": round(float(np.mean(bid[filled])) * 100, 2),
            # How much yield was left on the table when filled.
            "giveup_bps": round(float(np.mean((a - bid)[filled])) * 100, 2),
            "n": int(len(f)),
        })
    return out


def operating_point(forecast: np.ndarray, actual: np.ndarray,
                    target_fill: float = TARGET_FILL) -> dict | None:
    """The least shading that reaches `target_fill`, and what it captures.

    Least shading is the right choice: any extra shading beyond what is needed
    to hit the fill target is pure yield given away.
    """
    fr = frontier(forecast, actual)
    ok = [r for r in fr if r["fill_rate"] >= target_fill]
    if not ok:
        return None
    return min(ok, key=lambda r: r["shade_bps"])


def value_vs_benchmark(forecast: np.ndarray, bench: np.ndarray, actual: np.ndarray,
                       target_fill: float = TARGET_FILL) -> dict | None:
    """Yield pickup from bidding off `forecast` instead of `bench`.

    Both are shaded to the SAME fill rate, so both deploy the same notional and
    the difference is attributable to forecast accuracy alone.
    """
    a = operating_point(forecast, actual, target_fill)
    b = operating_point(bench, actual, target_fill)
    if a is None or b is None:
        return None
    return {
        "target_fill": target_fill,
        "model_shade_bps": a["shade_bps"], "bench_shade_bps": b["shade_bps"],
        "model_captured_bps": a["captured_on_filled_bps"],
        "bench_captured_bps": b["captured_on_filled_bps"],
        "pickup_bps": round(a["captured_on_filled_bps"] - b["captured_on_filled_bps"], 2),
        "n": a["n"],
    }


def annual_bdt(pickup_bps: float, notional_crore: float, auctions_per_year: float,
               tenor_years: float) -> float:
    """Translate a per-auction yield pickup into BDT crore a year.

    pickup_bps is an ANNUALISED yield difference, so the cash it earns on one
    auction is scaled by how long the money is actually invested (tenor_years),
    then by how many such auctions happen in a year.
    """
    per_auction = notional_crore * (pickup_bps / 10_000.0) * tenor_years
    return per_auction * auctions_per_year
