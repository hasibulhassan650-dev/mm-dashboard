"""Sukuk redemptions and profit are government cash into the market.

The ladder summed coupon + principal and later buyback; a product BB lists as
11 issues / 53,500 crore (5,000 cr of it maturing 30-Dec-2026) was in none of it.
Two rules decide whether this is right: profit is derived only where BB states a
half-yearly frequency (never guessed for "on Maturity" issues), and every cash
source lives in BOTH the ladder and the day detail or the two views disagree by
exactly that source.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DailyNetFlow
from engines.aggregation import build_daily_flows, sukuk_cash_flows
from liquidity_logic import day_detail, liquidity_ladder

D = datetime.date


def _issue(**kw):
    base = {"isin": "BDS1", "issue_date": D(2020, 12, 29), "maturity_date": D(2030, 12, 29),
            "outstanding_crore": 8000.0, "profit_rate_pct": 9.51, "profit_frequency": "Half Yearly"}
    base.update(kw)
    return base


WIDE = (D(2026, 1, 1), D(2031, 1, 31))


class TestWhatIsDerived:
    def test_principal_is_the_outstanding_amount_on_the_maturity_day(self):
        fl = sukuk_cash_flows([_issue(maturity_date=D(2026, 12, 30), outstanding_crore=5000.0,
                                      issue_date=D(2021, 12, 30), profit_rate_pct=None)],
                              D(2026, 12, 1), D(2026, 12, 31))
        p = [f for f in fl if f["kind"] == "PRINCIPAL"]
        assert len(p) == 1 and p[0]["amount_bdt_mill"] == 50000.0          # 5,000 cr = 50,000 mn
        assert p[0]["payment_date"] == D(2026, 12, 30)                      # a Wednesday

    def test_profit_is_half_the_annual_rate_on_each_six_month_anniversary(self):
        fl = sukuk_cash_flows([_issue()], D(2026, 1, 1), D(2026, 12, 31))
        prof = sorted((f["payment_date"], f["amount_bdt_mill"]) for f in fl if f["kind"] == "PROFIT")
        # 8,000 cr x 9.51% / 2 = 380.4 cr = 3,804 mn, on 29-Jun and 29-Dec
        assert [d for d, _ in prof] == [D(2026, 6, 29), D(2026, 12, 29)]
        assert all(abs(a - 3804.0) < 1e-6 for _, a in prof)

    def test_the_final_profit_coincides_with_maturity(self):
        fl = sukuk_cash_flows([_issue(issue_date=D(2025, 12, 29), maturity_date=D(2026, 12, 29),
                                      outstanding_crore=1000.0, profit_rate_pct=10.0)],
                              D(2026, 1, 1), D(2026, 12, 31))
        assert sorted(f["kind"] for f in fl if f["payment_date"] == D(2026, 12, 29)) == ["PRINCIPAL", "PROFIT"]

    def test_an_on_maturity_issue_gets_principal_only_never_an_invented_profit(self):
        fl = sukuk_cash_flows([_issue(issue_date=D(2026, 6, 29), maturity_date=D(2027, 3, 29),
                                      outstanding_crore=5500.0, profit_rate_pct=9.36,
                                      profit_frequency="on Maturity")], *WIDE)
        assert [f["kind"] for f in fl] == ["PRINCIPAL"]
        assert fl[0]["amount_bdt_mill"] == 55000.0

    def test_a_missing_rate_gives_no_profit_even_if_half_yearly(self):
        fl = sukuk_cash_flows([_issue(profit_rate_pct=None)], *WIDE)
        assert {f["kind"] for f in fl} == {"PRINCIPAL"}

    def test_a_weekend_or_holiday_rolls_to_the_next_working_day(self):
        """29-Dec-2028 is a Friday: the Bangladesh weekend. Cash arrives Sunday."""
        fl = sukuk_cash_flows([_issue(maturity_date=D(2028, 12, 29))], D(2028, 12, 1), D(2028, 12, 31))
        pays = [f["payment_date"] for f in fl if f["kind"] == "PRINCIPAL"]
        assert pays == [D(2028, 12, 31)] and pays[0].weekday() == 6

    def test_only_flows_inside_the_window_are_returned(self):
        fl = sukuk_cash_flows([_issue()], D(2026, 7, 1), D(2026, 11, 30))
        assert fl == []

    def test_an_unusable_issue_is_skipped_not_zeroed(self):
        assert sukuk_cash_flows([_issue(outstanding_crore=None)], *WIDE) == []
        assert sukuk_cash_flows([_issue(maturity_date=None)], *WIDE) == []


class TestTheDailyAggregate:
    def test_a_sukuk_day_appears_with_its_own_line_and_in_total_inflow(self):
        fl = sukuk_cash_flows([_issue(maturity_date=D(2026, 12, 30), issue_date=D(2021, 12, 30),
                                      outstanding_crore=5000.0, profit_rate_pct=None)],
                              D(2026, 12, 1), D(2026, 12, 31))
        rows = build_daily_flows([], [], [], D(2026, 12, 28), D(2026, 12, 31), sukuk_flows=fl)
        day = next(r for r in rows if r["flow_date"] == D(2026, 12, 30))
        assert day["sukuk_inflow_bdt_mill"] == 50000.0
        assert day["total_inflow_bdt_mill"] == 50000.0
        assert day["principal_inflow_bdt_mill"] == 0.0        # not mixed into bond principal

    def test_without_sukuk_nothing_changes(self):
        rows = build_daily_flows([], [], [], D(2026, 12, 28), D(2026, 12, 31))
        assert all(r["sukuk_inflow_bdt_mill"] == 0 for r in rows)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


class TestLadderAndDayDetailAgree:
    def _day(self):
        s = _mem()
        s.add(DailyNetFlow(flow_date=D(2026, 12, 30), coupon_inflow_bdt_mill=100.0,
                           principal_inflow_bdt_mill=0.0, buyback_inflow_bdt_mill=0.0,
                           sukuk_inflow_bdt_mill=50000.0, total_inflow_bdt_mill=50100.0,
                           auction_outflow_confirmed_mill=0.0, auction_outflow_best_mill=0.0,
                           data_complete=True))
        s.commit()
        return s

    def test_the_ladder_counts_it(self):
        s = self._day()
        p = liquidity_ladder(s, date_from="2026-12-30", date_to="2026-12-30", today=D(2026, 10, 2))
        d = p["days"][0]
        assert d["sukuk_inflow_crore"] == 5000.0
        assert d["govt_inflow_crore"] == pytest.approx(5010.0)

    def test_the_day_detail_agrees_with_the_ladder(self):
        """Two views of one day must never differ by a cash source."""
        s = self._day()
        det = day_detail(s, "2026-12-30")["liquidity"]
        lad = liquidity_ladder(s, date_from="2026-12-30", date_to="2026-12-30",
                               today=D(2026, 10, 2))["days"][0]
        assert det["sukuk_inflow_crore"] == lad["sukuk_inflow_crore"] == 5000.0
        assert det["govt_inflow_crore"] == lad["govt_inflow_crore"]
        assert det["total_net_crore"] == lad["net_crore"]

    def test_a_sukuk_maturing_outside_the_window_adds_nothing(self):
        s = self._day()
        d = liquidity_ladder(s, date_from="2026-12-31", date_to="2026-12-31",
                             today=D(2026, 10, 2))["days"][0]
        assert d["sukuk_inflow_crore"] == 0.0


def test_the_rebuild_reads_the_sukuk_table():
    """A flow computed but never wired into the rebuild would be a test of nothing."""
    src = open(os.path.join(_ROOT, "engines", "pipeline.py"), encoding="utf-8").read()
    assert "sukuk_cash_flows" in src and "sukuk_flows=sukuk_flows" in src
