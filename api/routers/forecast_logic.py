"""Pure shaping for the forecast API: the live track-record counts and the trust scoreboard.

No modelling and no scoring RULE lives here. Scoring (a forecast gets its actual_yield when a print exists on its own
target date) is done by forecast/run_forecast.py score_past_forecasts and is not touched. This module only COUNTS and
COMPARES what is already stored in auction_forecasts and backtest_metrics.

It lives in api/routers/ because the deployed API is rooted at api/ and cannot import the repo root (config, db,
engines, the forecast/ package). tests/test_forecast_scoreboard.py enforces that with an AST guard.
"""
import datetime

# Minimum scored auctions before a live figure is shown for a tenor. Duplicated from forecast/run_forecast.MIN_SCORED
# on purpose (this module cannot import it); tests/test_forecast_scoreboard.py fails if the two ever differ.
MIN_SCORED = 10

# Tie-break order when two established models have the same MAE: the order the research page lists them in
# (MODEL_ORDER in frontend/app/research/page.tsx).
MODEL_ORDER = ("naive", "momentum", "curve", "ecm", "ols", "blend")


def _d(v):
    """A date from a date, datetime or ISO string; None stays None."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


def recommended_model(by_model: dict) -> str | None:
    """The model the research page bids off for one tenor in one run.

    Same rule as bestModel() in frontend/app/research/page.tsx: the established non-naive model with the lowest MAE
    (ties in MODEL_ORDER), otherwise naive. `by_model` is {model: metric row with 'verdict' and 'mae_bps'}.
    None when the run has no naive row to fall back on and no established challenger.
    """
    proven = [m for m in MODEL_ORDER
              if m != "naive" and m in by_model
              and by_model[m].get("verdict") == "established" and by_model[m].get("mae_bps") is not None]
    if proven:
        return min(proven, key=lambda m: by_model[m]["mae_bps"])      # min is stable: first in MODEL_ORDER wins ties
    return "naive" if "naive" in by_model else None


def track_summary(groups, today) -> dict:
    """Counts for the 'Live Track Record' figure, in AUCTIONS.

    `groups` is one row per (tenor, target_auction_date) with `scored` truthy when ANY model's forecast for that
    auction has an actual_yield. The old figure counted stored tenor x model x run rows capped at 200, which is
    neither forecasts nor auctions. Here a scored auction is one (tenor, target date), however many models and
    runs forecast it. `overdue_unscored` are auctions whose target date has passed with no print matched: either the
    print has not been loaded yet or the auction did not happen on its estimated date (scoring matches the target
    date exactly, so a shifted auction is never scored).
    """
    today = _d(today)
    scored, overdue = set(), set()
    for g in groups:
        key = (g["tenor"], _d(g["target_auction_date"]))
        if key[1] is None:
            continue
        if g["scored"]:
            scored.add(key)
        elif key[1] < today:
            overdue.add(key)
    overdue -= scored           # a target some run scored is scored, whatever another run's row says
    return {
        "scored_auctions": len(scored),
        "overdue_unscored": len(overdue),
        "overdue_tenors": sorted({t for t, _ in overdue}),
    }


def scoreboard(forecasts, metrics, min_scored: int = MIN_SCORED) -> dict:
    """What the page actually published before each auction, against what printed. One row per tenor.

    `forecasts`: stored rows (tenor, model, target_auction_date, forecast_run_date, point_yield, lo_yield, hi_yield,
    actual_yield). `metrics`: backtest_metrics rows (computed_date, tenor, model, verdict, mae_bps, coverage).
    Only auction_forecasts feed the figures; backtest_metrics only says WHICH model the page recommended in the run
    that published, and what band coverage that run claimed for it.

    Rules, each of which exists because the other choice flatters the model:
      * a forecast counts only if its run date is strictly BEFORE its target date (made before the auction);
      * per (tenor, target, model) the LATEST such run counts: the forecast that stood when the auction came;
      * the model scored for an auction is the one the page recommended in THAT run (recommended_model on that run's
        metrics), compared with naive from the same run; an auction needs both, else it is not counted;
      * below `min_scored` auctions a tenor shows no figures at all ('insufficient'), only how many it has.
    """
    mets: dict = {}
    for m in metrics:
        mets.setdefault((_d(m["computed_date"]), m["tenor"]), {})[m["model"]] = m

    # latest pre-target run per (tenor, target, model)
    latest: dict = {}
    for f in forecasts:
        tgt, run = _d(f["target_auction_date"]), _d(f["forecast_run_date"])
        if tgt is None or run is None or run >= tgt or f["actual_yield"] is None:
            continue
        if any(f[k] is None for k in ("point_yield", "lo_yield", "hi_yield")):
            continue
        key = (f["tenor"], tgt, f["model"])
        if key not in latest or run > latest[key][0]:
            latest[key] = (run, f)

    auctions: dict = {}                      # (tenor, target) -> {model: (run, row)}
    for (tenor, tgt, model), v in latest.items():
        auctions.setdefault((tenor, tgt), {})[model] = v

    per_tenor: dict = {}
    for (tenor, tgt), models in sorted(auctions.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if "naive" not in models:
            continue
        run = models["naive"][0]
        rec = recommended_model(mets.get((run, tenor), {}))
        if rec is None or rec not in models or models[rec][0] != run:
            continue
        row = models[rec][1]
        actual = float(row["actual_yield"])
        err_rec = abs(float(row["point_yield"]) - actual) * 100
        err_naive = abs(float(models["naive"][1]["point_yield"]) - actual) * 100
        claim = (mets.get((run, tenor), {}).get(rec) or {}).get("coverage")
        per_tenor.setdefault(tenor, []).append({
            "target": tgt, "model": rec, "err": err_rec, "naive_err": err_naive,
            "inside": float(row["lo_yield"]) <= actual <= float(row["hi_yield"]),
            "claimed": claim,
        })

    out = []
    for tenor in sorted(per_tenor):
        a = per_tenor[tenor]
        n = len(a)
        row = {"tenor": tenor, "scored_auctions": n, "min_scored": min_scored,
               "status": "ok" if n >= min_scored else "insufficient",
               "recommended_model": a[-1]["model"], "live_mae_bps": None, "naive_mae_bps": None,
               "win_rate": None, "win_rate_n": 0, "inside_band": None, "claimed_coverage": None}
        if n >= min_scored:
            chal = [x for x in a if x["model"] != "naive"]
            claims = [x["claimed"] for x in a if x["claimed"] is not None]
            row.update({
                "live_mae_bps": round(sum(x["err"] for x in a) / n, 2),
                "naive_mae_bps": round(sum(x["naive_err"] for x in a) / n, 2),
                "win_rate": round(sum(x["err"] < x["naive_err"] for x in chal) / len(chal), 4) if chal else None,
                "win_rate_n": len(chal),
                "inside_band": round(sum(x["inside"] for x in a) / n, 4),
                "claimed_coverage": round(sum(claims) / len(claims), 4) if claims else None,
            })
        out.append(row)
    return {"min_scored": min_scored, "tenors": out}
