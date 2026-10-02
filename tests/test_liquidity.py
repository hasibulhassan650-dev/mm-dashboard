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
    day_detail, deal_effect, liquidity_ladder, liquidity_effect, net_outstanding,
    omo_maturity_ladder, resolve_window,
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
    """Four legs, and both directions of each. A tranche touches liquidity on
    its deal date and again, with the opposite sign, when it matures."""

    def test_a_new_injection_puts_cash_in_and_its_maturity_takes_it_back(self):
        assert deal_effect("INJECTION") == "INFLOW"
        assert liquidity_effect("INJECTION") == "OUTFLOW"

    def test_a_new_absorption_takes_cash_out_and_its_maturity_returns_it(self):
        assert deal_effect("ABSORPTION") == "OUTFLOW"
        assert liquidity_effect("ABSORPTION") == "INFLOW"

    def test_an_unknown_direction_is_consistent_across_both_legs(self):
        # It signs as an absorption, which is how it signs in the outstanding
        # stock. Calling it a drain on BOTH legs would be "conservative" but
        # would invent a permanent liquidity hole that grows with every such
        # row -- fabricated data is worse than a consistent guess. An unknown
        # instrument is validate.py's business.
        for d in (None, "SOMETHING_NEW"):
            assert deal_effect(d) == "OUTFLOW"
            assert liquidity_effect(d) == "INFLOW"

    def test_an_overnight_repo_nets_to_zero_across_its_two_days(self):
        eng, s = _mem()
        _omo(s, D(2026, 10, 5), D(2026, 10, 6), "CB_REPO", "INJECTION", 500.0, tenor="1D")
        s.commit()
        p = liquidity_ladder(s, date_from="2026-10-05", date_to="2026-10-06", today=TODAY)
        assert _day(p, "2026-10-05")["omo_net_crore"] == 500.0     # BB lends
        assert _day(p, "2026-10-06")["omo_net_crore"] == -500.0    # bank repays
        assert round(sum(d["omo_net_crore"] for d in p["days"]), 2) == 0.0

    def test_an_overnight_sdf_nets_to_zero_the_other_way_round(self):
        eng, s = _mem()
        _omo(s, D(2026, 10, 5), D(2026, 10, 6), "SDF", "ABSORPTION", 300.0, tenor="1D")
        s.commit()
        p = liquidity_ladder(s, date_from="2026-10-05", date_to="2026-10-06", today=TODAY)
        assert _day(p, "2026-10-05")["omo_net_crore"] == -300.0    # banks park cash
        assert _day(p, "2026-10-06")["omo_net_crore"] == 300.0     # BB returns it
        assert round(sum(d["omo_net_crore"] for d in p["days"]), 2) == 0.0

    def test_a_same_day_open_and_close_nets_to_zero_without_double_counting(self):
        eng, s = _mem()
        _omo(s, D(2026, 10, 1), D(2026, 10, 5), "CB_REPO", "INJECTION", 400.0)
        _omo(s, D(2026, 10, 5), D(2026, 10, 12), "CB_REPO", "INJECTION", 400.0)
        s.commit()
        row = _day(liquidity_ladder(s, date_from="2026-10-05", date_to="2026-10-05",
                                    today=TODAY), "2026-10-05")
        # one matures (out 400), one is dealt (in 400) -> flat, not -400 or +800
        assert row["omo_repay_crore"] == 400.0
        assert row["omo_new_inflow_crore"] == 400.0
        assert row["omo_net_crore"] == 0.0


class TestTheStockFlowInvariant:
    """net OMO flow(D) == net_outstanding(D) - net_outstanding(D-1).

    A fresh deal ENTERS BB's outstanding book and a maturing one LEAVES it, so
    the daily flow is the first difference of the stock. This is the test that
    makes the original bug unrepeatable: with only the maturity legs it fails on
    any day BB dealt, which was 11 of 15 days in Sep-2026.
    """

    def _seed(self, s):
        # several instruments, both directions, overlapping tenors
        _omo(s, D(2026, 10, 1), D(2026, 10, 8), "CB_REPO", "INJECTION", 12712.57)
        _omo(s, D(2026, 10, 1), D(2026, 10, 8), "IBLF", "INJECTION", 4078.78)
        _omo(s, D(2026, 10, 1), D(2026, 10, 2), "SDF", "ABSORPTION", 3390.00)
        _omo(s, D(2026, 10, 2), D(2026, 10, 3), "SDF", "ABSORPTION", 4089.00)
        _omo(s, D(2026, 10, 2), D(2026, 10, 9), "AR", "INJECTION", 90.00)
        _omo(s, D(2026, 10, 5), D(2026, 10, 6), "SDF", "ABSORPTION", 6434.00)
        _omo(s, D(2026, 10, 5), D(2026, 10, 19), "MLS", "INJECTION", 1330.00)
        _omo(s, D(2026, 10, 6), D(2026, 10, 13), "CM_REPO", "INJECTION", 182.00)
        s.commit()

    def test_the_flow_equals_the_change_in_the_stock_every_day(self):
        eng, s = _mem()
        self._seed(s)
        p = liquidity_ladder(s, date_from="2026-10-01", date_to="2026-10-20", today=TODAY)
        for row in p["days"]:
            day = D.fromisoformat(row["date"])
            delta = round(net_outstanding(s, day)
                          - net_outstanding(s, day - datetime.timedelta(days=1)), 2)
            assert abs(row["omo_net_crore"] - delta) < 0.02, (
                f"{row['date']}: flow {row['omo_net_crore']} vs stock change {delta}")

    def test_the_maturity_legs_alone_do_NOT_satisfy_it(self):
        # Guards the guard: if roll_net happened to equal the stock change, the
        # invariant above would pass on the broken code and prove nothing.
        eng, s = _mem()
        self._seed(s)
        p = liquidity_ladder(s, date_from="2026-10-01", date_to="2026-10-20", today=TODAY)
        mismatched, dealing = 0, 0
        for row in p["days"]:
            day = D.fromisoformat(row["date"])
            delta = round(net_outstanding(s, day)
                          - net_outstanding(s, day - datetime.timedelta(days=1)), 2)
            if abs(row["omo_roll_net_crore"] - delta) >= 0.02:
                mismatched += 1
            if row["omo_new_inflow_crore"] or row["omo_new_outflow_crore"]:
                dealing += 1
        # It diverges on exactly the days BB dealt -- which is the cause, not a
        # magic number. In Sep-2026 production that was 11 of 15 days.
        assert dealing > 0
        assert mismatched == dealing, (
            f"roll-off net diverged on {mismatched} days, BB dealt on {dealing}")

    def test_the_whole_window_nets_to_the_closing_stock(self):
        # Starting from nothing outstanding, the cumulative OMO flow across a
        # window that begins before any deal must equal the stock at the end.
        eng, s = _mem()
        self._seed(s)
        p = liquidity_ladder(s, date_from="2026-09-30", date_to="2026-10-20", today=TODAY)
        total = round(sum(d["omo_net_crore"] for d in p["days"]), 2)
        assert abs(total - net_outstanding(s, D(2026, 10, 20))) < 0.02


class TestTheSeptemberRegression:
    """The exact shape that was wrong in production on 2026-09-01: the page
    showed -21,161 crore of drain; the truth was -7,760."""

    def test_the_day_reads_minus_7760_not_minus_21161(self):
        eng, s = _mem()
        # dealt that day
        _omo(s, D(2026, 9, 1), D(2026, 9, 8), "CB_REPO", "INJECTION", 12712.57)
        _omo(s, D(2026, 9, 1), D(2026, 9, 8), "IBLF", "INJECTION", 4078.78)
        _omo(s, D(2026, 9, 1), D(2026, 9, 2), "SDF", "ABSORPTION", 3390.00)
        # maturing that day
        _omo(s, D(2026, 8, 25), D(2026, 9, 1), "CB_REPO", "INJECTION", 10619.57)
        _omo(s, D(2026, 8, 25), D(2026, 9, 1), "IBLF", "INJECTION", 4115.16)
        _omo(s, D(2026, 8, 31), D(2026, 9, 1), "AR", "INJECTION", 1798.29)
        _omo(s, D(2026, 8, 31), D(2026, 9, 1), "CB_REPO", "INJECTION", 7519.44)
        _omo(s, D(2026, 8, 31), D(2026, 9, 1), "SDF", "ABSORPTION", 2891.00)
        s.commit()
        row = _day(liquidity_ladder(s, date_from="2026-09-01", date_to="2026-09-01",
                                    today=TODAY), "2026-09-01")
        assert round(row["omo_roll_net_crore"]) == -21161      # what was shown
        assert round(row["omo_net_crore"]) == -7760            # what is true
        assert row["omo_net_crore"] != row["omo_roll_net_crore"]

    def test_a_day_whose_sign_flipped_is_now_right(self):
        # 09-Sep read +6,539 (flush, "cheap borrowing window") on a drain day.
        eng, s = _mem()
        _omo(s, D(2026, 9, 8), D(2026, 9, 9), "SDF", "ABSORPTION", 6629.00)   # matures in
        _omo(s, D(2026, 9, 1), D(2026, 9, 9), "AR", "INJECTION", 90.00)       # matures out
        _omo(s, D(2026, 9, 9), D(2026, 9, 10), "SDF", "ABSORPTION", 7071.00)  # dealt out
        _omo(s, D(2026, 9, 9), D(2026, 9, 16), "IBLF", "INJECTION", 157.00)   # dealt in
        s.commit()
        row = _day(liquidity_ladder(s, date_from="2026-09-09", date_to="2026-09-09",
                                    today=TODAY), "2026-09-09")
        assert row["omo_roll_net_crore"] > 0, "the old figure was positive"
        assert row["omo_net_crore"] < 0, "the true net is a drain"
        assert round(row["omo_net_crore"]) == -375


class TestCoverage:
    """Whether the FRESH-DEAL leg is known. Three different unknowns that must
    not be conflated, and none of which may render as zero."""

    def _seed(self, s):
        _omo(s, D(2026, 3, 5), D(2026, 3, 12), "CB_REPO", "INJECTION", 100.0)
        _omo(s, D(2026, 9, 29), D(2026, 10, 6), "CB_REPO", "INJECTION", 200.0)
        s.commit()

    def test_a_published_day_is_complete(self):
        eng, s = _mem(); self._seed(s)
        p = liquidity_ladder(s, date_from="2026-09-29", date_to="2026-09-29", today=TODAY)
        assert p["omo_dealt_to"] == "2026-09-29"
        assert _day(p, "2026-09-29")["omo_coverage"] == "complete"
        assert _day(p, "2026-09-29")["omo_complete"] is True

    def test_a_past_day_after_the_last_press_release_is_awaiting_publication(self):
        # It WILL fill in -- different from a future day, which never will.
        eng, s = _mem(); self._seed(s)
        p = liquidity_ladder(s, date_from="2026-09-30", date_to="2026-10-01", today=TODAY)
        assert all(d["omo_coverage"] == "awaiting_publication" for d in p["days"])
        assert all(d["omo_complete"] is False for d in p["days"])

    def test_a_future_day_is_future(self):
        eng, s = _mem(); self._seed(s)
        p = liquidity_ladder(s, date_from="2026-10-20", date_to="2026-10-21", today=TODAY)
        assert all(d["omo_coverage"] == "future" for d in p["days"])

    def test_a_day_before_records_begin_has_no_data(self):
        eng, s = _mem(); self._seed(s)
        p = liquidity_ladder(s, date_from="2026-01-10", date_to="2026-01-11", today=TODAY)
        assert all(d["omo_coverage"] == "no_data" for d in p["days"])
        assert all(d["omo_known"] is False for d in p["days"])


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
        assert liq["omo_roll_net_crore"] == round(1200.0 - 3280.04, 2)
        assert liq["omo_net_crore"] == round(1200.0 - 3280.04, 2)   # nothing dealt that day
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

    def test_the_ladder_reports_both_the_roll_off_and_the_true_flow(self, seeded):
        # The old single "net" was the roll-off presented as the flow.
        _, s = seeded
        _omo(s, D(2026, 10, 4), D(2026, 10, 20), "CB_REPO", "INJECTION", 9000.0)
        s.commit()
        d4 = _day(omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31",
                                      today=TODAY), "2026-10-04")
        assert d4["roll_net_crore"] == round(-(1671.21 + 1330.0), 2)
        assert d4["new_inflow_crore"] == 9000.0
        assert d4["net_crore"] == round(d4["roll_net_crore"] + 9000.0, 2)
        assert d4["new_by_instrument"]["CB_REPO"] == 9000.0

    def test_a_day_with_only_fresh_deals_still_appears(self, seeded):
        # Before the fix the ladder was keyed on maturity dates alone, so a day
        # where BB only DEALT was missing from it entirely.
        _, s = seeded
        _omo(s, D(2026, 10, 15), D(2026, 11, 15), "AR", "INJECTION", 777.0)
        s.commit()
        p = omo_maturity_ladder(s, date_from="2026-10-01", date_to="2026-10-31", today=TODAY)
        row = _day(p, "2026-10-15")
        assert row["new_inflow_crore"] == 777.0
        assert row["by_instrument"] == {}

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
