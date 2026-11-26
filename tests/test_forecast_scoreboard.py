"""The live track record counts auctions, and the trust scoreboard says what was published against what printed.

Why these exist. 'Live Track Record' showed the number of stored tenor x model x run ROWS, capped at 200: a figure that
looked like evidence and was neither forecasts nor auctions. The scoreboard is the one check a backtest cannot be, so
every rule in it exists because the other choice flatters the model. The logic lives in api/routers/forecast_logic.py
because the deployed API is rooted at api/ and cannot import the repo root (an AST guard below enforces that).
"""
import ast
import datetime
import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
# Appended (not inserted) so repo-root modules still win: api/db.py must never shadow the real db module here.
sys.path.append(os.path.join(_ROOT, "api", "routers"))
sys.path.append(os.path.join(_ROOT, "api"))

import forecast_logic as fl  # noqa: E402

D = datetime.date


# ── deployment boundary ──────────────────────────────────────────────────────

def test_the_logic_imports_nothing_from_the_repo_root():
    """config, db, engines and the forecast/ package all live above api/ and are not uploaded with it."""
    forbidden = {"config", "db", "engines", "calendar_utils", "validate", "fetchers", "parsers", "forecast"}
    src = io.open(fl.__file__, encoding="utf-8").read()
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in forbidden:
                bad.append(node.module)
    assert not bad, f"api/routers/forecast_logic.py cannot import from the repo root: {bad}"


def test_the_duplicated_minimum_agrees_with_the_engines():
    """The API cannot import run_forecast.MIN_SCORED, so it is copied; a copy that drifts would show figures the
    engine itself refuses to publish."""
    src = io.open(os.path.join(_ROOT, "forecast", "run_forecast.py"), encoding="utf-8").read()
    assert fl.MIN_SCORED == int(re.search(r"^MIN_SCORED\s*=\s*(\d+)", src, re.M).group(1))


def test_the_page_does_not_count_the_capped_display_rows():
    page = io.open(os.path.join(_ROOT, "frontend", "app", "research", "page.tsx"), encoding="utf-8").read()
    assert "track_record.length" not in page, "the KPI must come from track_summary (auctions), not capped rows"
    assert "track_summary" in page and "forecastScoreboard" in page


# ── the KPI ──────────────────────────────────────────────────────────────────

def _g(tenor, target, scored):
    return {"tenor": tenor, "target_auction_date": target, "scored": scored}


def test_a_scored_auction_is_counted_once_however_many_models_and_runs_forecast_it():
    """Six models over three runs is eighteen stored rows and ONE auction."""
    groups = [_g("2Y", D(2026, 9, 1), 1)]                     # the API already groups by (tenor, target)
    assert fl.track_summary(groups, D(2026, 11, 1))["scored_auctions"] == 1


def test_forecasts_past_their_date_with_no_print_are_reported_not_hidden():
    groups = [_g("2Y", D(2026, 9, 1), 1), _g("5Y", D(2026, 9, 2), 0), _g("10Y", D(2026, 12, 1), 0),
              _g("2Y", None, 0)]
    out = fl.track_summary(groups, D(2026, 11, 1))
    assert out == {"scored_auctions": 1, "overdue_unscored": 1, "overdue_tenors": ["5Y"]}


def test_an_auction_one_run_scored_is_not_also_called_overdue():
    groups = [_g("2Y", "2026-09-01", 0), _g("2Y", "2026-09-01", 1)]
    out = fl.track_summary(groups, "2026-11-01")
    assert out["scored_auctions"] == 1 and out["overdue_unscored"] == 0


# ── which model the page recommends ──────────────────────────────────────────

def _m(model, verdict, mae):
    return {"model": model, "verdict": verdict, "mae_bps": mae}


def test_the_recommended_model_is_an_established_challenger_with_the_lowest_mae_else_naive():
    """The rule the page bids off (bestModel in research/page.tsx): not simply the lowest MAE, which is selection bias."""
    by = {"naive": _m("naive", "benchmark", 40), "curve": _m("curve", "established", 25),
          "blend": _m("blend", "established", 30), "momentum": _m("momentum", "unproven", 5)}
    assert fl.recommended_model(by) == "curve"
    assert fl.recommended_model({"naive": by["naive"], "momentum": by["momentum"]}) == "naive"
    assert fl.recommended_model({"curve": _m("curve", "unproven", 1)}) is None


# ── the scoreboard, over a synthetic world ───────────────────────────────────

def _fc(tenor, model, run, target, point, actual, half=0.20):
    return {"tenor": tenor, "model": model, "forecast_run_date": run, "target_auction_date": target,
            "point_yield": point, "lo_yield": point - half, "hi_yield": point + half, "actual_yield": actual}


def _world(n_2y=12, n_5y=3):
    """2Y: curve is established and the page's pick; it misses by 0.10 against naive's 0.30 and always lands inside
    its +/-0.20 band. 5Y: only a few auctions. Runs are on Thursdays, targets the following Tuesday."""
    forecasts, metrics = [], []
    for i in range(n_2y):
        run, tgt = D(2026, 1, 1) + datetime.timedelta(days=28 * i), D(2026, 1, 6) + datetime.timedelta(days=28 * i)
        forecasts += [_fc("2Y", "naive", run, tgt, 9.0, 9.30), _fc("2Y", "curve", run, tgt, 9.2, 9.30)]
        metrics += [{"computed_date": run, "tenor": "2Y", "model": "naive", "verdict": "benchmark", "mae_bps": 30, "coverage": 0.9},
                    {"computed_date": run, "tenor": "2Y", "model": "curve", "verdict": "established", "mae_bps": 10, "coverage": 0.93}]
    for i in range(n_5y):
        run, tgt = D(2026, 1, 1) + datetime.timedelta(days=28 * i), D(2026, 1, 6) + datetime.timedelta(days=28 * i)
        forecasts += [_fc("5Y", "naive", run, tgt, 9.0, 9.1)]
        metrics += [{"computed_date": run, "tenor": "5Y", "model": "naive", "verdict": "benchmark", "mae_bps": 20, "coverage": 0.9}]
    return forecasts, metrics


def _row(board, tenor):
    return next(r for r in board["tenors"] if r["tenor"] == tenor)


def test_live_figures_are_those_of_the_recommended_model_against_naive():
    board = fl.scoreboard(*_world())
    r = _row(board, "2Y")
    assert r["status"] == "ok" and r["scored_auctions"] == 12 and r["recommended_model"] == "curve"
    assert r["live_mae_bps"] == 10.0 and r["naive_mae_bps"] == 30.0
    assert r["win_rate"] == 1.0 and r["win_rate_n"] == 12
    assert r["inside_band"] == 1.0 and r["claimed_coverage"] == 0.93


def test_a_tenor_below_the_minimum_shows_a_count_and_no_figures_at_all():
    """Three auctions are noise wearing a decimal point."""
    r = _row(fl.scoreboard(*_world()), "5Y")
    assert r["status"] == "insufficient" and r["scored_auctions"] == 3 and r["min_scored"] == fl.MIN_SCORED
    assert all(r[k] is None for k in ("live_mae_bps", "naive_mae_bps", "win_rate", "inside_band", "claimed_coverage"))


def test_a_band_that_did_not_hold_is_visible_against_the_coverage_it_claimed():
    forecasts, metrics = _world()
    for f in forecasts:
        if f["tenor"] == "2Y" and f["model"] == "curve" and f["target_auction_date"].toordinal() % 2 == 0:
            f["actual_yield"] = f["point_yield"] + 0.5            # well outside +/-0.20
    r = _row(fl.scoreboard(forecasts, metrics), "2Y")
    assert r["inside_band"] < r["claimed_coverage"]


def test_a_forecast_made_on_or_after_its_auction_is_never_counted():
    """A run on the auction's own day, or after it, can have seen the print: hindsight, not a track record."""
    forecasts, metrics = _world()
    for f in forecasts:
        if f["tenor"] == "2Y":
            f["forecast_run_date"] = f["target_auction_date"]
    assert "2Y" not in {r["tenor"] for r in fl.scoreboard(forecasts, metrics)["tenors"]}


def test_when_several_runs_forecast_one_auction_the_latest_one_before_it_counts_once():
    forecasts, metrics = _world(n_2y=10, n_5y=0)
    extra = []
    for f in list(forecasts):
        # an earlier, worse run for the same auction, with its own metrics: must be superseded, not double counted
        early = dict(f, forecast_run_date=f["forecast_run_date"] - datetime.timedelta(days=7), point_yield=f["point_yield"] + 1)
        extra.append(early)
    metrics += [dict(m, computed_date=m["computed_date"] - datetime.timedelta(days=7)) for m in metrics]
    r = _row(fl.scoreboard(forecasts + extra, metrics), "2Y")
    assert r["scored_auctions"] == 10 and r["live_mae_bps"] == 10.0


def test_the_model_scored_is_the_one_recommended_in_the_run_that_published():
    """If the page said naive that week, naive is what was followed, whatever it recommends now."""
    forecasts, metrics = _world(n_2y=10, n_5y=0)
    for m in metrics:
        if m["model"] == "curve":
            m["verdict"] = "unproven"
    r = _row(fl.scoreboard(forecasts, metrics), "2Y")
    assert r["recommended_model"] == "naive" and r["live_mae_bps"] == 30.0 and r["win_rate"] is None


def test_an_auction_with_no_naive_forecast_alongside_is_not_counted():
    forecasts, metrics = _world(n_2y=10, n_5y=0)
    forecasts = [f for f in forecasts if f["model"] != "naive"]
    assert fl.scoreboard(forecasts, metrics)["tenors"] == []


# ── the SQL, against the real routes ─────────────────────────────────────────

def test_the_scoreboard_route_reads_only_the_published_log(monkeypatch):
    """Run the route's own SQL on SQLite, so a query that only worked on paper is caught."""
    pytest.importorskip("fastapi")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db as dbmod
    from db import AuctionForecast, BacktestMetric
    import routers.forecast as route

    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    forecasts, metrics = _world()
    for f in forecasts:
        s.add(AuctionForecast(**f))
    for m in metrics:
        s.add(BacktestMetric(**m))
    s.commit()
    monkeypatch.setattr(route, "get_session", lambda: Session())
    out = route.get_scoreboard()
    assert _row(out, "2Y")["scored_auctions"] == 12 and _row(out, "2Y")["live_mae_bps"] == 10.0
    assert _row(out, "5Y")["status"] == "insufficient" and out["min_scored"] == fl.MIN_SCORED
