"""The liquidity ladder over any window, the day breakdown, and the OMO ladder.

The test that matters most here is the sign convention. `direction` on an OMO
row describes the ORIGINAL operation, but at maturity the effect reverses: an
SDF (absorption) maturing pays cash back to banks, while a repo (injection)
maturing takes cash out. Getting that backwards inverts the whole ladder and
would tell the desk to lend on exactly the days it should be borrowing. Several
tests below exist only to pin it down.

The second theme is coverage: `omo_transactions` begins 2026-03-05, so a window
reaching earlier has NO OMO data. That is not the same as BB running no
operations, and the payload has to let the UI say which it is.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import AuctionEvent, DailyNetFlow, OMOTransaction

sys.path.append(os.path.join(_ROOT, "api", "routers"))
from liquidity_logic import (  # noqa: E402
    day_detail, liquidity_ladder, liquidity_effect, omo_maturity_ladder, resolve_window,
)

D = datetime.date
TODAY = D(2026, 10, 2)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _omo(s, txn, mat, instrument, direction, crore, tenor="7D", rate=10.0):
    s.add(OMOTransaction(transaction_date=txn, maturity_date=mat, instrument=instrument,
                         direction=direction, accepted_bdt_crore=crore, tenor_label=tenor,
                         tenor_days=7, rate_pct=rate))


def _flow(s, d, coupon_mill=0.0, principal_mill=0.0, auction_mill=0.0):
    s.add(DailyNetFlow(flow_date=d, coupon_inflow_bdt_mill=coupon_mill,
                       principal_inflow_bdt_mill=principal_mill,
                       total_inflow_bdt_mill=coupon_mill + principal_mill,
                       auction_outflow_confirmed_mill=auction_mill,
                       auction_outflow_best_mill=auction_mill, data_complete=True))


def _auction(s, settle, mill=1000.0):
    s.add(AuctionEvent(fiscal_year="2026-27", auction_date=settle - datetime.timedelta(days=1),
                       settlement_date=settle, security_type="T_BILL", tenor_label="91D",
                       offered_amount_bdt_mill=mill, accepted_amount_bdt_mill=mill,
                       outflow_status="CONFIRMED", weighted_avg_yield_pct=10.0))


def _day(p, date):
    return next(d for d in p["days"] if d["date"] == date)


class TestTheSignConvention:
    """An OMO's effect at maturity is the reverse of its original direction."""

    def test_absorption_maturing_is_an_inflow(self):
        assert liquidity_effect("ABSORPTION") == "INFLOW"

    def test_injection_maturing_is_an_outflow(self):
        assert liquidity_effect("INJECTION") == "OUTFLOW"

    def test_an_sdf_maturing_adds_liquidity_and_a_repo_maturing_drains_it(self):
        # SDF 500 back to banks, repo 300 repaid to BB -> net +200.
        eng, s = _mem()
        _omo(s, D(2026, 10, 1), D(2026, 10, 5), "SDF", "ABSORPTION", 500.0)
        _omo(s, D(2026, 10, 1), D(2026, 10, 5), "CB_REPO", "INJECTION", 300.0)
        s.commit()
        row = _day(liquidity_ladder(s, date_from="2026-10-05", date_to="2026-10-05",
                                    today=TODAY), "2026-10-05")
        assert row["omo_return_crore"] == 500.0
        assert row["omo_repay_crore"] == 300.0
        assert row["omo_net_crore"] == 200.0
        assert row["net_crore"] == 200.0

    def test_an_unknown_direction_is_treated_as_a_drain(self):
        # Fail safe: an unrecognised instrument counted as an inflow would
        # overstate available cash, which is the dangerous direction to be wrong.
        assert liquidity_effect(None) == "OUTFLOW"
        assert liquidity_effect("SOMETHING_NEW") == "OUTFLOW"


class TestWindow:
    def test_the_legacy_days_horizon_is_unchanged(self):
        # Existing callers must not shift by a day when ranges arrive.
        f, t = resolve_window(days=28, today=TODAY)
        assert f == D(2026, 10, 3) and t == D(2026, 10, 30)

    def test_an_explicit_range_may_be_entirely_in_the_past(self):
        f, t = resolve_window(date_from="2026-04-01", date_to="2026-04-30", today=TODAY)
        assert f == D(2026, 4, 1) and t == D(2026, 4, 30)

    @pytest.mark.parametrize("kw", [
        {"date_from": "2026-05-01", "date_to": "2026-04-01"},        # reversed
        {"date_from": "2020-01-01", "date_to": "2026-01-01"},        # absurdly wide
    ])
    def test_a_nonsense_range_is_refused(self, kw):
        with pytest.raises(ValueError):
            resolve_window(today=TODAY, **kw)

    def test_history_returns_the_days_asked_for(self):
        eng, s = _mem()
        _omo(s, D(2026, 4, 1), D(2026, 4, 8), "CB_REPO", "INJECTION", 100.0)
        s.commit()
        p = liquidity_ladder(s, date_from="2026-04-05", date_to="2026-04-10", today=TODAY)
        assert [d["date"] for d in p["days"]] == [
            "2026-04-05", "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"]
        assert all(d["is_past"] for d in p["days"])
        assert _day(p, "2026-04-08")["omo_repay_crore"] == 100.0


class TestOmoCoverageIsNotZero:
    """omo_transactions starts 2026-03-05. Before that we know nothing, which is
    a different claim from "BB ran no operations" -- and the ladder's net would
    silently understate both directions."""

    def test_a_day_before_the_data_starts_is_marked_unknown(self):
        eng, s = _mem()
        _omo(s, D(2026, 3, 5), D(2026, 3, 12), "CB_REPO", "INJECTION", 100.0)
        _flow(s, D(2026, 1, 15), coupon_mill=500.0)
        s.commit()
        p = liquidity_ladder(s, date_from="2026-01-10", date_to="2026-03-13", today=TODAY)
        assert p["omo_data_from"] == "2026-03-05"
        assert _day(p, "2026-01-15")["omo_known"] is False
        assert _day(p, "2026-03-12")["omo_known"] is True

    def test_the_ladder_reports_where_every_source_starts_and_stops(self):
        eng, s = _mem()
        _omo(s, D(2026, 3, 5), D(2026, 3, 12), "CB_REPO", "INJECTION", 100.0)
        _auction(s, D(2026, 12, 28))
        _flow(s, D(2026, 10, 5))
        s.commit()
        p = liquidity_ladder(s, days=7, today=TODAY)
        assert p["omo_data_from"] == "2026-03-05"
        assert p["auction_horizon"] == "2026-12-28"
        assert p["flows_data_from"] == "2026-10-05"


class TestMaturityOnlyRowsAreExcluded:
    def test_a_maturity_only_row_does_not_double_book(self):
        # BB prints a maturity-only line (accepted = 0, maturity == txn date)
        # describing what matured. The tranche itself is already stored, so
        # counting both would book the roll-off twice.
        eng, s = _mem()
        _omo(s, D(2026, 9, 28), D(2026, 10, 5), "CB_REPO", "INJECTION", 400.0)
        _omo(s, D(2026, 10, 5), D(2026, 10, 5), "CB_REPO", "INJECTION", 0.0)
        s.commit()
        row = _day(liquidity_ladder(s, date_from="2026-10-05", date_to="2026-10-05",
                                    today=TODAY), "2026-10-05")
        assert row["omo_repay_crore"] == 400.0


class TestCumulative:
    def test_cumulative_is_the_running_sum_of_the_day_nets(self):
        eng, s = _mem()
        _omo(s, D(2026, 10, 1), D(2026, 10, 4), "SDF", "ABSORPTION", 100.0)
        _omo(s, D(2026, 10, 1), D(2026, 10, 5), "CB_REPO", "INJECTION", 60.0)
        _omo(s, D(2026, 10, 1), D(2026, 10, 6), "SDF", "ABSORPTION", 30.0)
        s.commit()
        p = liquidity_ladder(s, date_from="2026-10-04", date_to="2026-10-06", today=TODAY)
        assert [d["net_crore"] for d in p["days"]] == [100.0, -60.0, 30.0]
        assert [d["cum_net_crore"] for d in p["days"]] == [100.0, 40.0, 70.0]

    def test_cumulative_re_anchors_to_the_window_not_to_hidden_history(self):
        # Starting the window a day later must not carry in the earlier day --
        # a running total that includes rows the caller cannot see is misleading.
        eng, s = _mem()
        _omo(s, D(2026, 10, 1), D(2026, 10, 4), "SDF", "ABSORPTION", 100.0)
        _omo(s, D(2026, 10, 1), D(2026, 10, 5), "CB_REPO", "INJECTION", 60.0)
        s.commit()
        p = liquidity_ladder(s, date_from="2026-10-05", date_to="2026-10-05", today=TODAY)
        assert p["days"][0]["cum_net_crore"] == -60.0


class TestDayDetail:
    @pytest.fixture
    def seeded(self):
        eng, s = _mem()
        _omo(s, D(2026, 10, 4), D(2026, 10, 11), "AR", "INJECTION", 3098.04, tenor="7D")
        _omo(s, D(2026, 10, 4), D(2026, 10, 11), "CM_REPO", "INJECTION", 182.0)
        _omo(s, D(2026, 10, 10), D(2026, 10, 11), "SDF", "ABSORPTION", 1200.0, tenor="1D")
        _flow(s, D(2026, 10, 11), coupon_mill=500.0, principal_mill=1500.0, auction_mill=10000.0)
        _auction(s, D(2026, 10, 11), mill=10000.0)
        s.commit()
        return eng, s

    def test_every_omo_product_is_listed_with_its_effect(self, seeded):
        _, s = seeded
        d = day_detail(s, "2026-10-11")
        got = {r["instrument"]: (r["liquidity_effect"], r["crore"]) for r in d["omo"]}
        assert got == {"AR": ("OUTFLOW", 3098.04), "CM_REPO": ("OUTFLOW", 182.0),
                       "SDF": ("INFLOW", 1200.0)}

    def test_the_netted_lines_tie_to_the_products(self, seeded):
        _, s = seeded
        liq = day_detail(s, "2026-10-11")["liquidity"]
        assert liq["omo_outflow_crore"] == round(3098.04 + 182.0, 2)
        assert liq["omo_inflow_crore"] == 1200.0
        assert liq["omo_net_crore"] == round(1200.0 - 3280.04, 2)
        assert liq["govt_inflow_crore"] == 200.0          # (500 + 1500) mill -> crore
        assert liq["auction_outflow_crore"] == 1000.0     # 10,000 mill -> crore
        assert liq["total_net_crore"] == round(
            1200.0 - 3280.04 + 200.0 - 1000.0, 2)

    def test_it_agrees_with_the_ladder_for_the_same_day(self, seeded):
        # The two are separate code paths over the same rows; if they ever
        # disagree the page and its drilldown would show different numbers.
        _, s = seeded
        row = _day(liquidity_ladder(s, date_from="2026-10-11", date_to="2026-10-11",
                                    today=TODAY), "2026-10-11")
        liq = day_detail(s, "2026-10-11")["liquidity"]
        assert row["omo_return_crore"] == liq["omo_inflow_crore"]
        assert row["omo_repay_crore"] == liq["omo_outflow_crore"]
        assert row["auction_out_crore"] == liq["auction_outflow_crore"]
        assert row["govt_inflow_crore"] == liq["govt_inflow_crore"]
        assert row["net_crore"] == liq["total_net_crore"]

    def test_a_day_with_no_omo_is_empty_not_broken(self, seeded):
        _, s = seeded
        d = day_detail(s, "2026-10-12")
        assert d["omo"] == [] and d["liquidity"]["omo_net_crore"] == 0.0
        assert d["liquidity"]["total_net_crore"] == 0.0


class TestOmoMaturityLadder:
    @pytest.fixture
    def seeded(self):
        eng, s = _mem()
        _omo(s, D(2026, 10, 1), D(2026, 10, 4), "IBLF", "INJECTION", 1671.21)
        _omo(s, D(2026, 10, 1), D(2026, 10, 4), "MLS", "INJECTION", 1330.0)
        _omo(s, D(2026, 10, 1), D(2026, 10, 6), "CB_REPO", "INJECTION", 8846.26)
        _omo(s, D(2026, 10, 5), D(2026, 10, 6), "SDF", "ABSORPTION", 2000.0)
        s.commit()
        return eng, s

    def test_each_day_breaks_down_by_product(self, seeded):
        _, s = seeded
        p = omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31", today=TODAY)
        d4 = _day(p, "2026-10-04")
        assert d4["by_instrument"] == {"IBLF": 1671.21, "MLS": 1330.0}
        assert d4["outflow_crore"] == round(1671.21 + 1330.0, 2)
        assert d4["inflow_crore"] == 0.0

    def test_both_directions_net_on_the_same_day(self, seeded):
        _, s = seeded
        d6 = _day(omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31",
                                      today=TODAY), "2026-10-06")
        assert d6["inflow_crore"] == 2000.0
        assert d6["outflow_crore"] == 8846.26
        assert d6["net_crore"] == round(2000.0 - 8846.26, 2)

    def test_cumulative_tracks_the_day_nets(self, seeded):
        _, s = seeded
        p = omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31", today=TODAY)
        run = 0.0
        for d in p["days"]:
            run = round(run + d["net_crore"], 2)
            assert d["cum_net_crore"] == run

    def test_totals_tie_to_the_days(self, seeded):
        _, s = seeded
        p = omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31", today=TODAY)
        assert p["total_outflow_crore"] == round(sum(d["outflow_crore"] for d in p["days"]), 2)
        assert p["total_net_crore"] == round(sum(d["net_crore"] for d in p["days"]), 2)
        assert p["instrument_totals"]["CB_REPO"] == 8846.26

    def test_an_unknown_instrument_still_appears(self, seeded):
        # #1 rule on this project: never lose a product. A new BB facility must
        # show up rather than be filtered out by a hardcoded list.
        _, s = seeded
        _omo(s, D(2026, 10, 1), D(2026, 10, 7), "BRAND_NEW_FACILITY", "INJECTION", 42.0)
        s.commit()
        p = omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31", today=TODAY)
        assert "BRAND_NEW_FACILITY" in p["instruments"]
        assert _day(p, "2026-10-07")["by_instrument"]["BRAND_NEW_FACILITY"] == 42.0

    def test_maturity_only_rows_are_excluded_here_too(self, seeded):
        _, s = seeded
        _omo(s, D(2026, 10, 4), D(2026, 10, 4), "IBLF", "INJECTION", 0.0)
        s.commit()
        p = omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31", today=TODAY)
        assert _day(p, "2026-10-04")["by_instrument"] == {"IBLF": 1671.21, "MLS": 1330.0}

    def test_the_default_window_includes_today(self, seeded):
        # A tranche maturing today is still today's problem.
        _, s = seeded
        p = omo_maturity_ladder(s, days=30, today=D(2026, 10, 4))
        assert p["from"] == "2026-10-04"
        assert any(d["date"] == "2026-10-04" for d in p["days"])


class TestTheDeploymentBoundary:
    def test_liquidity_logic_imports_nothing_from_the_repo_root(self):
        # Same hazard that returned a production 500 for the schedule: the API
        # is deployed rooted at api/ and cannot see config, db or engines.
        import ast
        import io as _io

        import liquidity_logic
        forbidden = {"config", "db", "engines", "calendar_utils", "validate",
                     "fetchers", "parsers"}
        src = _io.open(liquidity_logic.__file__, encoding="utf-8").read()
        bad = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in forbidden:
                    bad.append(node.module)
        assert not bad, f"liquidity_logic cannot import from the repo root: {bad}"
