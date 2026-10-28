"""Every tenor BB auctions flows through every view, and a tenor that stopped auctioning stops being 'today'.

Two things went wrong together. The yields page's long-end KPI read 10.05%: the curve returned the latest
row of EVERY tenor ever auctioned, so a 14-day bill last auctioned in Sep-2025 (and a 28-day bill from 2008)
sat on today's curve, and the KPI took the highest yield while captioning it with the longest tenor. And the
14-day bill was missing from Explore, the correlation and the desk form because each kept its own fixed list of
tenors. The cure is that nothing lists tenors: they come from the data, and the guard below fails the build if
a fixed list comes back.
"""
import datetime
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import PrimaryYieldSnapshot

D = datetime.date
NEWEST = D(2026, 10, 25)


@pytest.fixture
def yields(monkeypatch):
    import yields as mod
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    monkeypatch.setattr(mod, "get_session", lambda: sessionmaker(bind=eng)())
    return mod, s


def _row(s, label, years, typ, yld, when):
    s.add(PrimaryYieldSnapshot(snapshot_date=when, auction_date=when, issue_date=when, security_type=typ,
                               tenor_label=label, tenor_years=years, cutoff_yield_pct=yld, source="BB"))


def _world(s):
    _row(s, "91D", 0.25, "T_BILL", 8.56, NEWEST)
    _row(s, "364D", 1.0, "T_BILL", 8.79, NEWEST)
    _row(s, "20Y", 20.0, "T_BOND", 9.385, D(2026, 10, 27))
    _row(s, "2Y", 2.0, "T_BOND", 8.25, D(2026, 10, 6))                 # three weeks old: still live
    _row(s, "14D", 14 / 365, "T_BILL", 10.05, D(2025, 9, 17))          # a year old
    _row(s, "28D", 28 / 365, "T_BILL", 7.5, D(2008, 6, 19))            # eighteen years old
    s.commit()


class TestTheCurveIsToday:
    def test_a_tenor_that_stopped_auctioning_is_not_on_the_curve(self, yields):
        mod, s = yields
        _world(s)
        labels = [r["tenor_label"] for r in mod.get_yield_curve()]
        assert labels == ["91D", "364D", "2Y", "20Y"], labels
        assert max(r["cutoff_yield_pct"] for r in mod.get_yield_curve() if r["security_type"] == "T_BILL") < 10

    def test_but_it_is_listed_as_dormant_not_dropped_silently(self, yields):
        mod, s = yields
        _world(s)
        dormant = {r["tenor_label"]: r for r in mod.get_dormant_tenors()}
        assert set(dormant) == {"14D", "28D"} and dormant["14D"]["cutoff_yield_pct"] == 10.05

    def test_it_comes_back_by_itself_when_bb_auctions_it_again(self, yields):
        """No one edits a list: a fresh 14D auction puts the tenor on the curve."""
        mod, s = yields
        _world(s)
        _row(s, "14D", 14 / 365, "T_BILL", 9.9, D(2026, 10, 20)); s.commit()
        row = next(r for r in mod.get_yield_curve() if r["tenor_label"] == "14D")
        assert row["cutoff_yield_pct"] == 9.9 and row["age_days"] == 7
        assert "14D" not in {r["tenor_label"] for r in mod.get_dormant_tenors()}

    def test_the_curve_is_ordered_shortest_first_and_empty_data_is_empty(self, yields):
        mod, s = yields
        assert mod.get_yield_curve() == [] and mod.get_dormant_tenors() == []
        _world(s)
        yrs = [r["tenor_years"] for r in mod.get_yield_curve()]
        assert yrs == sorted(yrs)


FRONTEND = os.path.join(_ROOT, "frontend")


def _src(*parts):
    return open(os.path.join(FRONTEND, *parts), encoding="utf-8").read()


class TestNoViewKeepsItsOwnListOfTenors:
    """Why this is a test: a list of tenors is correct the day it is written and wrong the day BB changes
    what it auctions - and the failure is silence (a series that never appears). So the lists are banned."""

    def test_explore_builds_its_yield_series_from_the_history(self):
        src = _src("app", "explore", "page.tsx")
        assert not re.search(r'label:\s*"\d+[DY][^"]*(T-Bill|T-Bond|FRTB)"', src), \
            "Explore lists tenors by hand again - build them from the auction history"
        assert "yieldSeries" in src and "...yieldSeries" in src

    def test_the_ticker_shows_every_live_tenor_not_a_chosen_few(self):
        src = _src("app", "layout.tsx")
        assert not re.search(r'for \(const t of \[\s*"91D"', src)

    def test_the_trend_chart_and_research_page_do_not_keep_tenor_tables(self):
        assert "TENOR_ORDER" not in _src("components", "YieldTrendChart.tsx")
        assert "TENOR_YEARS" not in _src("app", "research", "page.tsx")

    def test_the_end_of_the_curve_kpi_shows_the_yield_of_the_tenor_it_names(self):
        src = _src("app", "yields", "page.tsx")
        assert "Math.max(...vals)" not in src and "Math.min(...vals)" not in src, \
            "a 'long end' KPI that takes the highest yield is not the longest tenor's yield"

    def test_a_carried_forward_print_has_a_shelf_life_in_the_correlation(self):
        src = _src("components", "ExploreTool.tsx")
        assert "carryDays" in src and "limit" in src
