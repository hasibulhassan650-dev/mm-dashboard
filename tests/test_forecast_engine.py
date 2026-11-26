"""The forecast engine may not publish a number whose claim it did not earn.

Each test is named for the harm it prevents. A synthetic world (a weekly bill anchor on Sundays, a monthly bond
on Tuesdays) is stored in an in-memory SQLite database and run through the real run_forecast.run() in dry-run
mode, so the checks cover the engine as published, not a re-implementation of it.
"""
import datetime
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import AuctionEvent, PrimaryYieldSnapshot

D = datetime.date


@pytest.fixture(autouse=True)
def _the_forecast_package_not_the_api_router():
    """Other tests put api/routers on sys.path, where forecast.py (the API router) shadows the root forecast/ package
    for the rest of the run. Import the package this file is about, then put the path back as found."""
    saved_path, saved_mods = list(sys.path), {k: v for k, v in sys.modules.items() if k == "forecast" or k.startswith("forecast.")}
    if "forecast" in sys.modules and not hasattr(sys.modules["forecast"], "__path__"):
        for k in saved_mods:
            del sys.modules[k]
    sys.path[:] = [_ROOT] + [p for p in sys.path if os.path.basename(os.path.normpath(p)) != "routers"]
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "forecast" or k.startswith("forecast.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


# ── sequential coverage ──────────────────────────────────────────────────────

def _shifting_errors():
    """Calm for 40 auctions, then the market turns violent for 10. In percent, like the engine's errors."""
    rng = np.random.default_rng(11)
    return np.concatenate([rng.normal(0, 0.05, 40), rng.normal(0, 0.40, 10)])


def test_in_sample_coverage_hides_a_volatility_shift_that_sequential_coverage_exposes():
    """The old figure built the band from ALL errors and then counted those same errors inside it, so the violent
    stretch (which inflated the band) looked covered. Judged as it would have been drawn, it was not."""
    from forecast import robustness as rb
    e = _shifting_errors()
    in_sample = float(np.mean(np.abs(e) <= rb.band_half_width(e)))
    sequential = rb.sequential_coverage(e)
    assert in_sample > 0.95, in_sample
    assert sequential < in_sample - 0.05, (sequential, in_sample)


def test_sequential_coverage_counts_each_error_against_the_band_of_the_errors_before_it():
    """Hand-checkable: five tiny errors seed the band, then one the same size (hit) and one far outside (miss)."""
    from forecast import robustness as rb
    e = np.array([0.01, -0.01, 0.01, -0.01, 0.01, 0.01, 5.0])
    assert rb.sequential_coverage(e) == 0.5           # i=5 inside, i=6 outside, out of the 2 scorable


def test_the_band_a_forecast_is_judged_against_never_depends_on_later_errors():
    """No look-ahead: changing the LAST error cannot change whether any earlier error was a hit."""
    from forecast import robustness as rb
    e = _shifting_errors()
    a = rb.sequential_coverage(e[:-1])
    changed = e.copy()
    changed[-1] = 99.0
    assert rb.sequential_coverage(changed[:-1]) == a


def test_too_few_errors_give_no_coverage_rather_than_a_made_up_one():
    from forecast import robustness as rb
    assert rb.sequential_coverage(np.array([0.1, 0.2, 0.1, 0.2, 0.1])) is None


def test_dead_code_stays_deleted():
    """Two unused functions that disagreed with the ones in use (a normal-reference DM, an in-sample coverage)."""
    from forecast import robustness as rb, run_forecast as rf
    assert not hasattr(rf, "diebold_mariano")
    assert not hasattr(rb, "coverage")


# ── the ECM gate ─────────────────────────────────────────────────────────────

def _corridor(*points):
    return pd.DataFrame({"effective_date": [pd.Timestamp(p[0]) for p in points],
                         "repo": [p[1] for p in points], "slf": None, "sdf": None, "verified": True})


def test_a_corridor_that_starts_in_june_2023_still_opens_the_gate_for_a_five_year_bond_window():
    """BB had no corridor before 2023-06-20. Measuring coverage back to the start of a 5-year window would block
    the ECM forever on a condition reality cannot meet; coverage is the corridor's own age."""
    from forecast.policy import usable
    hist = _corridor(("2023-06-20", 6.0), ("2024-02-01", 8.0), ("2025-02-01", 10.0), ("2026-02-01", 9.5))
    five_years_back = pd.Timestamp.today() - pd.Timedelta(days=5 * 365)
    ok, why = usable(hist, five_years_back)
    assert ok, why


def test_a_corridor_younger_than_two_years_still_blocks_the_gate():
    """The guard above must be able to fail."""
    from forecast.policy import usable
    start = pd.Timestamp.today() - pd.Timedelta(days=300)
    hist = _corridor((start, 6.0), (start + pd.Timedelta(days=60), 7.0),
                     (start + pd.Timedelta(days=120), 8.0), (start + pd.Timedelta(days=180), 9.0))
    ok, _ = usable(hist, start)
    assert not ok


# ── the ECM verdict ──────────────────────────────────────────────────────────

def _coint(**tenor_p):
    return [{"tenor": t, "test": "coint", "subject": "cutoff~repo", "pvalue": p} for t, p in tenor_p.items()]


def test_the_ecm_is_never_called_rejected_while_any_tenor_is_cointegrated():
    """The page used to print REJECTED and 'on any tenor' as typed text. A verdict that is typed cannot change
    when the evidence does. Whatever the p-values, REJECTED may not coexist with one below the threshold."""
    from forecast.run_diagnostics import ecm_verdict, ALPHA
    rng = np.random.default_rng(5)
    for _ in range(300):
        ps = {f"T{i}": float(p) for i, p in enumerate(rng.uniform(0, 1, rng.integers(1, 9)))}
        word = ecm_verdict("2026-01-01", True, "open", _coint(**ps))["conclusion"].split(" - ")[0]
        if any(p < ALPHA for p in ps.values()):
            assert word == "SUPPORTED", (ps, word)
        else:
            assert word == "REJECTED", (ps, word)


def test_the_stored_cointegration_p_values_give_rejected_and_a_supported_tenor_is_named():
    from forecast.run_diagnostics import ecm_verdict
    stored = _coint(**{"10Y": 0.7436, "15Y": 0.8415, "182D": 0.964, "2Y": 0.9626, "91D": 0.967})
    assert ecm_verdict("2026-01-01", True, "open", stored)["conclusion"].startswith("REJECTED - ")
    v = ecm_verdict("2026-01-01", True, "open", stored + _coint(**{"5Y": 0.03}))
    assert v["conclusion"].startswith("SUPPORTED - ") and "5Y" in v["conclusion"]
    assert v["pvalue"] == 0.03 and v["tenor"] == "ALL" and v["test"] == "ecm_verdict"


def test_a_shut_gate_is_blocked_and_an_open_gate_with_no_test_is_not_tested_never_rejected():
    from forecast.run_diagnostics import ecm_verdict
    assert ecm_verdict("2026-01-01", False, "only 2 verified corridor change(s)", [])["conclusion"].startswith("BLOCKED - only 2")
    assert ecm_verdict("2026-01-01", True, "open", [])["conclusion"].startswith("NOT TESTED - ")
    # an ADF row on the spread is not a cointegration test
    adf = [{"tenor": "2Y", "test": "adf", "subject": "spread_policy", "pvalue": 0.9}]
    assert ecm_verdict("2026-01-01", True, "open", adf)["conclusion"].startswith("NOT TESTED - ")


def test_the_research_page_no_longer_types_the_ecm_verdict():
    src = open(os.path.join(_ROOT, "frontend", "app", "research", "page.tsx"), encoding="utf-8").read()
    assert "Tested and Rejected" not in src
    assert "on any tenor" not in src
    assert "ecm_verdict" in src


# ── bills are decided by what they are ───────────────────────────────────────

def test_a_new_bill_tenor_gets_the_bill_window_because_the_window_follows_the_security_type():
    """BB starting a 7-day bill must not silently be fitted on the 5-year bond window."""
    from forecast import run_forecast as rf
    assert rf.lookback_for("T_BILL") == rf.LOOKBACK_YEARS
    assert rf.lookback_for("T_BOND") == rf.LOOKBACK_BONDS
    assert rf.lookback_for("FRTB") == rf.LOOKBACK_BONDS
    assert rf.lookback_for(None) == rf.LOOKBACK_BONDS


def test_the_published_spec_names_the_bill_instrument_not_a_list_of_tenors():
    from forecast.method_spec import spec
    s = spec()
    assert s["bill_instrument"] == "T_BILL" and "bill_tenors" not in s


# ── the synthetic world, through the real engine ─────────────────────────────

def _world():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    rng = np.random.default_rng(3)

    def add(label, years, typ, when, yld, accepted, cover):
        s.add(PrimaryYieldSnapshot(snapshot_date=when, auction_date=when, issue_date=when + datetime.timedelta(days=1),
                                   security_type=typ, tenor_label=label, tenor_years=years, cutoff_yield_pct=yld,
                                   offered_bdt_crore=accepted * cover, accepted_bdt_crore=accepted, source="test"))
    y = 9.0
    d = D(2023, 8, 6)                              # a Sunday: the bill anchor auctions weekly
    while d <= D(2026, 11, 15):
        y += rng.normal(0, 0.08)
        add("364D", 1.0, "T_BILL", d, round(y, 4), 1000 + rng.uniform(0, 200), 1.5 + rng.uniform(0, 1))
        d += datetime.timedelta(days=7)
    yb = 9.5
    d = D(2022, 9, 6)                              # a Tuesday: the bond auctions every 28 days
    for _ in range(50):
        yb += rng.normal(0, 0.25)
        add("2Y", 2.0, "T_BOND", d, round(yb, 4), 800 + rng.uniform(0, 200), 1.5 + rng.uniform(0, 1))
        d += datetime.timedelta(days=28)
    # BB's calendar: one earlier date (already printed) and a next auction one week AFTER the median-gap estimate
    for when in (D(2026, 5, 12), D(2026, 7, 14), D(2026, 8, 11)):
        s.add(AuctionEvent(fiscal_year="2025-26", auction_no=f"B{when.month}", auction_date=when, tenor_label="2Y",
                           security_type="T_BOND"))
    s.commit()
    return Session


@pytest.fixture(scope="module")
def world():
    return _world()


def _run(world, monkeypatch, **flags):
    from forecast import run_forecast as rf
    monkeypatch.setattr(rf, "get_session", lambda: world())
    for k, v in flags.items():
        monkeypatch.setattr(rf, k, v)
    out = rf.run(dry_run=True, return_rows=True)
    return ({(r["tenor"], r["model"]): r for r in out["forecast_rows"]},
            {(r["tenor"], r["model"]): r for r in out["metric_rows"]})


def _same(a, b, ignore):
    assert a.keys() == b.keys()
    for k in a:
        for f in a[k]:
            if f not in ignore:
                assert a[k][f] == b[k][f], (k, f, a[k][f], b[k][f])


def test_a_model_with_too_few_scored_forecasts_publishes_no_mae_or_band(world, monkeypatch):
    """The 5Y ECM went out with n = 2: a MAE and a 100% 'band holds' from two auctions. The tenor-level gate only
    looked at the first model that ran, so a model starting later slipped past it."""
    from forecast import run_forecast as rf
    from forecast.features import build as build_features, build_anchor
    fc, mt = _run(world, monkeypatch)
    hist = rf.load_history(world(), lookback_years=5)
    g = hist[hist.tenor_label == "2Y"]
    f = rf.attach_anchor(build_features(g), build_anchor(hist, "364D"))
    bt = rf.backtest(f)
    # the trap exists in this world: OLS starts late and scores fewer than MIN_SCORED forecasts on the bond
    assert 0 < len(bt["ols"]["err"]) < rf.MIN_SCORED <= len(bt["naive"]["err"])
    assert ("2Y", "ols") not in mt and ("2Y", "ols") not in fc
    assert ("2Y", "naive") in mt
    assert all(m["n_obs"] >= rf.MIN_SCORED for m in mt.values())


def test_the_stored_curve_lambda_is_the_one_that_produced_the_published_point(world, monkeypatch):
    """The lambda used to live only in the job log. Stored now, and the published point must be last + that stored
    lambda x the anchor move, or the stored number does not describe the forecast it sits beside."""
    from forecast import run_forecast as rf
    from forecast.features import build as build_features, build_anchor
    fc, mt = _run(world, monkeypatch)
    hist = rf.load_history(world(), lookback_years=5)
    anchor = build_anchor(hist, "364D")
    g = hist[hist.tenor_label == "2Y"].sort_values("auction_date")
    f = rf.attach_anchor(build_features(g), anchor)
    live = rf._next_row(f, anchor)
    lam = mt[("2Y", "curve")]["curve_lambda"]
    assert lam == round(rf.curve_lambda(f), 6)
    assert fc[("2Y", "curve")]["point_yield"] == pytest.approx(
        float(live["cutoff_lag1"]) + lam * float(live["d_anchor"]), abs=2e-4)
    # lambda belongs to the curve model only; and the anchor tenor has no curve model at all
    assert all(m["curve_lambda"] is None for (t, mod), m in mt.items() if mod != "curve")
    assert ("364D", "curve") not in mt


def test_the_stored_coverage_is_sequential_not_in_sample(world, monkeypatch):
    from forecast import run_forecast as rf, robustness as rb
    from forecast.features import build as build_features, build_anchor
    _, mt = _run(world, monkeypatch)
    hist = rf.load_history(world(), lookback_years=5)
    g = hist[hist.tenor_label == "2Y"]
    f = rf.attach_anchor(build_features(g), build_anchor(hist, "364D"))
    err = np.asarray(rf.backtest(f)["naive"]["err"], dtype=float)
    assert mt[("2Y", "naive")]["coverage"] == rb.sequential_coverage(err)


def test_every_published_row_carries_the_method_that_produced_it(world, monkeypatch):
    fc, mt = _run(world, monkeypatch)
    assert {r["method_version"] for r in fc.values()} == {"r2;anchor_as_of_run=0;target_from_calendar=0"}
    assert {r["method_version"] for r in mt.values()} == {"r2;anchor_as_of_run=0;target_from_calendar=0"}


# ── ANCHOR_AS_OF_RUN and TARGET_FROM_CALENDAR ────────────────────────────────

def test_the_run_day_is_the_thursday_the_workflow_runs_on():
    from forecast import run_forecast as rf
    cron = re.search(r'cron:\s*"([^"]+)"', open(os.path.join(_ROOT, ".github", "workflows", "forecast_weekly.yml"),
                                                encoding="utf-8").read()).group(1).split()
    # cron counts Sunday as 0 (or 7); Python counts Monday as 0
    assert (int(cron[4]) - 1) % 7 == rf.RUN_WEEKDAY, cron


def test_each_auction_is_paired_with_the_run_that_precedes_it():
    from forecast import run_forecast as rf
    dates = pd.Series(pd.to_datetime(["2026-03-08", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-15"]))
    assert [d.date() for d in rf.last_run_date(dates)] == [
        D(2026, 3, 5), D(2026, 3, 5), D(2026, 3, 5), D(2026, 3, 12), D(2026, 3, 12)]


def _bond_frame():
    return pd.DataFrame({"auction_date": pd.to_datetime(["2026-02-10", "2026-03-10"])})


def _anchor():
    return pd.DataFrame({"auction_date": pd.to_datetime(["2026-02-01", "2026-02-08", "2026-03-01", "2026-03-08"]),
                         "anchor_yield": [7.0, 7.5, 8.0, 9.0]})


def test_the_backtest_anchor_can_be_limited_to_what_the_published_forecast_knew(monkeypatch):
    """The Tuesday 10-Mar bond is forecast on Thursday 5-Mar. Sunday 8-Mar's bill print is unknown then, yet the
    default backtest hands it to the model. With the switch on it is withheld."""
    from forecast import run_forecast as rf
    monkeypatch.setattr(rf, "ANCHOR_AS_OF_RUN", False)
    off = rf.attach_anchor(_bond_frame(), _anchor())
    monkeypatch.setattr(rf, "ANCHOR_AS_OF_RUN", True)
    on = rf.attach_anchor(_bond_frame(), _anchor())
    assert list(off["anchor_now"]) == [7.5, 9.0]      # last print strictly before each auction
    assert list(on["anchor_now"]) == [7.0, 8.0]       # last print strictly before each preceding run day
    assert on["d_anchor"].iloc[1] == pytest.approx(1.0) and off["d_anchor"].iloc[1] == pytest.approx(1.5)


def test_a_bill_print_on_the_run_day_itself_is_not_known_at_the_run(monkeypatch):
    """The run is 04:00 UTC on the day; that day's auction result is not in it. Strictly before, never on."""
    from forecast import run_forecast as rf
    monkeypatch.setattr(rf, "ANCHOR_AS_OF_RUN", True)
    anchor = pd.DataFrame({"auction_date": pd.to_datetime(["2026-03-01", "2026-03-05"]), "anchor_yield": [8.0, 9.9]})
    got = rf.attach_anchor(pd.DataFrame({"auction_date": pd.to_datetime(["2026-03-10"])}), anchor)
    assert got["anchor_now"].iloc[0] == 8.0


def test_the_target_comes_from_bbs_calendar_only_when_the_switch_is_on(monkeypatch):
    from forecast import run_forecast as rf
    dates = pd.Series(pd.to_datetime(["2026-04-14", "2026-05-12", "2026-06-09"]))
    calendar = [D(2026, 5, 12), D(2026, 7, 14), D(2026, 8, 11)]
    monkeypatch.setattr(rf, "TARGET_FROM_CALENDAR", False)
    assert rf.choose_target(dates, calendar) == (D(2026, 7, 7), "estimated")      # last print + median gap
    monkeypatch.setattr(rf, "TARGET_FROM_CALENDAR", True)
    assert rf.choose_target(dates, calendar) == (D(2026, 7, 14), "calendar")
    # the NEXT scheduled auction after the last print, not the next one in the future: a missing print stays the target
    assert rf.choose_target(dates, [D(2026, 6, 16), D(2026, 7, 14)]) == (D(2026, 6, 16), "calendar")
    # the calendar has run out: fall back to the estimate and say so
    assert rf.choose_target(dates, [D(2026, 5, 12)]) == (D(2026, 7, 7), "estimated")
    assert rf.choose_target(dates, None) == (D(2026, 7, 7), "estimated")


def test_flipping_the_calendar_switch_changes_the_target_date_and_nothing_else(world, monkeypatch):
    """If any forecast number moves when only the date source changes, the date has leaked into the model."""
    fc0, mt0 = _run(world, monkeypatch)
    fc1, mt1 = _run(world, monkeypatch, TARGET_FROM_CALENDAR=True)
    _same(fc0, fc1, ignore={"target_auction_date", "method_version"})
    _same(mt0, mt1, ignore={"method_version"})
    assert fc0[("2Y", "naive")]["target_auction_date"] == D(2026, 7, 7)
    assert fc1[("2Y", "naive")]["target_auction_date"] == D(2026, 7, 14)
    assert fc1[("2Y", "naive")]["method_version"].endswith("target_from_calendar=1")


def test_the_as_of_run_anchor_moves_the_bond_curve_forecast_and_leaves_the_anchor_tenor_alone(world, monkeypatch):
    """The anchor tenor never uses an anchor, and naive never does either: neither may move. The bond's curve-based
    numbers must (Sunday's print falls between the Thursday run and the Tuesday auction in this world)."""
    fc0, mt0 = _run(world, monkeypatch)
    fc1, mt1 = _run(world, monkeypatch, ANCHOR_AS_OF_RUN=True)
    for k in mt0:
        if k[0] == "364D" or k[1] in ("naive", "momentum", "ols"):
            assert {f: v for f, v in mt0[k].items() if f != "method_version"} == \
                   {f: v for f, v in mt1[k].items() if f != "method_version"}, k
            assert fc0[k]["point_yield"] == fc1[k]["point_yield"], k
    assert (mt0[("2Y", "curve")]["mae_bps"] != mt1[("2Y", "curve")]["mae_bps"]
            or fc0[("2Y", "curve")]["point_yield"] != fc1[("2Y", "curve")]["point_yield"])
    assert fc1[("2Y", "curve")]["method_version"].startswith("r2;anchor_as_of_run=1")


def test_the_committed_spec_states_the_switches_the_code_runs_with():
    from forecast.method_spec import spec
    committed = json.loads(open(os.path.join(_ROOT, "frontend", "lib", "forecastMethod.json"), encoding="utf-8").read())
    for k in ("anchor_as_of_run", "target_from_calendar", "run_weekday", "method_version", "bill_instrument"):
        assert committed[k] == spec()[k], k
