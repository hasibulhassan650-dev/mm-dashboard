"""The liquidity insights engine: every verdict is computed, dated and honest about what it does not know.

Each test says WHY it matters. The two that matter most: an unknown is never a zero (an unpublished auction calendar is not 'no borrowing'),
and news-derived events never change a number (cash a desk trades against must come from BB's own record).
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import insights_logic as I

D = datetime.date
CORRIDOR = {"repo": 9.5, "slf": 11.0, "sdf": 7.5, "effective_date": "2026-09-01"}


def _call(rate, provisional=False):
    return {"rate": rate, "date": "2026-11-19", "provisional": provisional}


class TestTheRegimeIsDecidedByPriceInsideTheCorridor:
    def test_call_money_well_below_repo_is_surplus(self):
        r = I.regime(_call(9.23), CORRIDOR, None)
        assert r["label"] == I.SURPLUS and r["position_in_corridor_pct"] == 49.4

    def test_call_money_just_inside_the_band_of_repo_is_balanced(self):
        assert I.regime(_call(9.27), CORRIDOR, None)["label"] == I.BALANCED

    def test_call_money_near_the_ceiling_or_above_repo_is_tight(self):
        assert I.regime(_call(10.6), CORRIDOR, None)["label"] == I.TIGHT       # within 50bp of the SLF
        assert I.regime(_call(9.8), CORRIDOR, None)["label"] == I.TIGHT        # 30bp above repo

    def test_the_rule_is_shown_with_every_verdict(self):
        """The desk must see the rule it is being judged by: the thresholds are constants, not hidden."""
        assert "SLF" in I.regime(_call(9.3), CORRIDOR, None)["rule"]["tight"]

    def test_a_missing_input_makes_it_unknown_never_a_guess(self):
        assert I.regime(None, CORRIDOR, None)["label"] == I.UNKNOWN
        assert I.regime(_call(9.2), None, None)["label"] == I.UNKNOWN
        assert I.regime(_call(9.2), {"repo": 9.5, "slf": None, "sdf": 7.5}, None)["label"] == I.UNKNOWN

    def test_a_provisional_print_is_labelled(self):
        assert "provisional" in " ".join(I.regime(_call(9.2, provisional=True), CORRIDOR, None)["why"])

    def test_fresh_lending_beside_sdf_parking_is_flagged_as_a_two_speed_market_not_netted(self):
        """18-Nov: BB lent 8,260 cr of IBLF while 4,556 cr was parked at the SDF. Netting them would hide that both are happening."""
        r = I.regime(_call(9.23), CORRIDOR, {"date": "2026-11-18", "injection_crore": 8260.0, "sdf_crore": 4556.0})
        assert r["segmented"] is True and "not netted" in " ".join(r["why"])

    def test_small_flows_are_not_called_a_split(self):
        assert I.regime(_call(9.23), CORRIDOR, {"date": "x", "injection_crore": 300.0, "sdf_crore": 5000.0})["segmented"] is False


class TestDollarOperationsHaveASignAndAnUnknownIsNotGuessed:
    def test_a_bb_purchase_adds_taka_and_a_sale_drains_it(self):
        buy = I.fx_taka_effect([{"auction_date": "2026-11-10", "auction_type": "Buy", "accepted_amount_usd_mill": 100, "weighted_avg_rate": 124.5}])
        sell = I.fx_taka_effect([{"auction_date": "2026-11-10", "auction_type": "Sell", "accepted_amount_usd_mill": 100, "weighted_avg_rate": 124.5}])
        assert buy["crore"] == 1245.0 and sell["crore"] == -1245.0, "USD mn x rate / 10 = BDT crore"

    def test_no_auction_is_none_not_zero(self):
        assert I.fx_taka_effect([])["crore"] is None

    def test_an_unrecognised_type_is_listed_and_not_summed(self):
        r = I.fx_taka_effect([{"auction_date": "d", "auction_type": "Swap", "accepted_amount_usd_mill": 50, "weighted_avg_rate": 124.0}])
        assert r["crore"] is None and r["unknown_types"] == ["Swap"]


class TestWhatMovedThisWeek:
    def test_a_facility_whose_stock_moved_is_listed_biggest_first_and_small_moves_are_not(self):
        now = [{"instrument": "IBLF", "direction": "INJECTION", "outstanding_crore": 60000.0},
               {"instrument": "SDF", "direction": "ABSORPTION", "outstanding_crore": 4500.0},
               {"instrument": "AR", "direction": "INJECTION", "outstanding_crore": 3100.0}]
        then = [{"instrument": "IBLF", "direction": "INJECTION", "outstanding_crore": 52000.0},
                {"instrument": "SDF", "direction": "ABSORPTION", "outstanding_crore": 4400.0},
                {"instrument": "AR", "direction": "INJECTION", "outstanding_crore": 3000.0},
                {"instrument": "SLS", "direction": "INJECTION", "outstanding_crore": 1500.0}]
        got = I.stock_movers(now, then)
        assert [m["instrument"] for m in got] == ["IBLF", "SLS"], "SDF and AR moved under 1,000 cr; SLS vanished"
        assert got[0]["change_crore"] == 8000.0 and got[1]["change_crore"] == -1500.0

    def test_securities_cash_is_none_when_any_working_day_is_unconfirmed(self):
        """A partial sum would look complete."""
        days = [{"flows_confirmed": True, "govt_inflow_crore": 1000.0, "auction_out_crore": 400.0},
                {"flows_confirmed": False, "govt_inflow_crore": 50.0, "auction_out_crore": 0.0}]
        assert I.securities_net(days)["crore"] is None
        assert I.securities_net(days[:1])["crore"] == 600.0

    def test_weekly_drivers_keep_components_apart(self):
        omo_now = {"totals": {"net_injected_crore": 56000.0}, "instruments": []}
        omo_then = {"totals": {"net_injected_crore": 50000.0}, "instruments": []}
        d = I.week_drivers(omo_now, omo_then, [], I.fx_taka_effect([]), _call(9.23), {"rate": 9.40})
        assert d["bb_net_lending_change_crore"] == 6000.0 and d["call_change_bp"] == -17.0
        assert d["fx"]["crore"] is None and d["securities"]["crore"] is None


def _ladder(days):
    return [{"date": str(d), "flows_confirmed": c, "govt_inflow_crore": i, "auction_out_crore": o, "omo_roll_net_crore": r}
            for d, c, i, o, r in days]


class TestTheNextWorkingDays:
    TODAY = D(2026, 11, 19)        # a Thursday

    def test_weekend_and_holidays_are_skipped_and_a_holiday_is_shown_as_one(self):
        cal = I.flow_calendar([], {D(2026, 11, 23)}, D(2026, 12, 31), self.TODAY, 4)
        assert [c["date"] for c in cal] == ["2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"], "Fri 20 and Sat 21 are skipped"
        assert cal[1]["holiday"] is True and cal[1]["securities_net_crore"] is None

    def test_a_day_past_the_published_auction_calendar_has_an_unknown_net_not_zero(self):
        """BB auctions weekly; a missing calendar is not 'no borrowing'."""
        ladder = _ladder([(D(2026, 11, 22), True, 100.0, 0.0, 0.0), (D(2026, 11, 29), False, 100.0, 0.0, 0.0)])
        cal = I.flow_calendar(ladder, set(), D(2026, 11, 24), self.TODAY, 7)
        by = {c["date"]: c for c in cal}
        assert by["2026-11-22"]["securities_net_crore"] == 100.0
        assert by["2026-11-29"]["securities_net_crore"] is None and "not published" in by["2026-11-29"]["basis"]

    def test_the_net_is_inflows_minus_auction_settlements_and_offered_amounts_are_labelled(self):
        ladder = _ladder([(D(2026, 11, 22), False, 800.0, 6500.0, -9000.0)])
        row = I.flow_calendar(ladder, set(), D(2026, 12, 31), self.TODAY, 1)[0]
        assert row["securities_net_crore"] == -5700.0 and "offered" in row["basis"]
        assert row["omo_rolloff_crore"] == -9000.0, "OMO roll-off is shown but is NOT in the securities net"

    def test_dated_news_appears_on_its_day_but_changes_no_number(self):
        ladder = _ladder([(D(2026, 11, 22), True, 100.0, 0.0, 0.0)])
        base = I.flow_calendar(ladder, set(), D(2026, 12, 31), self.TODAY, 1)[0]
        with_news = I.flow_calendar(ladder, set(), D(2026, 12, 31), self.TODAY, 1,
                                    events=[{"id": 1, "when": "2026-11-22", "amount_crore": 99999.0}])[0]
        assert with_news["events"] and with_news["securities_net_crore"] == base["securities_net_crore"] == 100.0


class TestTheRankedStatements:
    def _stmts(self, calendar=(), news=None, omo=None, drivers=None, reg=None):
        reg = reg or I.regime(_call(9.23), CORRIDOR, None)
        drivers = drivers or {"call_change_bp": None, "movers": [], "fx": {"crore": None}, "securities": {"crore": None}}
        return I.rank_insights(reg, drivers, list(calendar), omo, news or {"events": []}, "2026-09-15", "2026-11-12", "2026-12-01")

    def test_every_statement_carries_numbers_and_evidence_and_alerts_come_first(self):
        cal = [{"date": "2026-11-24", "weekday": "Tue", "securities_net_crore": -5800.0, "govt_inflow_crore": 200.0,
                "auction_out_crore": 6000.0, "basis": "calendar (offered amounts; BB may accept less)", "holiday": False}]
        out = self._stmts(cal)
        assert out[0]["severity"] == "alert" and "drain" in out[0]["headline"] and "5,800" in out[0]["headline"]
        for s in out:
            assert {"id", "severity", "headline", "detail", "numbers", "evidence"} <= set(s)

    def test_a_small_day_is_not_an_alarm(self):
        cal = [{"date": "2026-11-24", "weekday": "Tue", "securities_net_crore": -900.0, "govt_inflow_crore": 0.0,
                "auction_out_crore": 900.0, "basis": "confirmed", "holiday": False}]
        assert not [s for s in self._stmts(cal) if s["id"].startswith("drain_")]

    def test_the_coverage_statement_says_what_the_view_does_not_know(self):
        cov = [s for s in self._stmts() if s["id"] == "coverage"][0]
        assert "2026-11-12" in cov["detail"] and "2026-12-01" in cov["detail"] and "never zero" in cov["detail"]

    def test_an_unknown_regime_is_a_watch_item_not_silence(self):
        out = self._stmts(reg=I.regime(None, None, None))
        assert out[0]["id"] == "regime" and out[0]["severity"] == "watch" and "cannot be judged" in out[0]["headline"]

    def test_a_huge_news_event_changes_no_number_in_the_regime_drivers_or_calendar(self):
        """News is shown beside the figures, never inside them (the guard the ladder already has, applied to this module)."""
        news = {"events": [{"id": 5, "summary": "Govt releases Tk 99,999 crore", "amount_crore": 99999.0, "when": "2026-11-22"}]}
        a, b = self._stmts(), self._stmts(news=news)
        assert [s for s in b if s["id"].startswith("news_")] and not [s for s in a if s["id"].startswith("news_")]
        assert [s for s in a if not s["id"].startswith("news_")] == [s for s in b if not s["id"].startswith("news_")]


class TestTheDatabaseGlue:
    def _mem(self):
        eng = create_engine("sqlite:///:memory:")
        dbmod.Base.metadata.create_all(eng)
        return sessionmaker(bind=eng)()

    def test_an_empty_database_gives_an_unknown_verdict_and_no_crash(self):
        out = I.insights_block(self._mem(), today=D(2026, 11, 19))
        assert out["regime"]["label"] == I.UNKNOWN and out["insights"][0]["id"] == "regime"
        assert out["sign_convention"].startswith("flows: + adds cash")

    def test_with_call_money_and_the_corridor_it_judges_the_market(self):
        s = self._mem()
        s.add(dbmod.PolicyRateSnapshot(first_seen_date=D(2026, 9, 1), repo=9.5, slf=11.0, sdf=7.5))
        for d, rate in ((D(2026, 11, 19), 9.23), (D(2026, 11, 12), 9.40)):
            s.add(dbmod.CallMoneyRate(trade_date=d, product="Overnight", maturity_days=1, amount_crore=1000.0, average_rate_pct=rate))
        s.commit()
        out = I.insights_block(s, today=D(2026, 11, 19))
        assert out["regime"]["label"] == I.SURPLUS
        assert out["drivers"]["call_change_bp"] == -17.0
        assert out["regime"]["inputs"]["call"]["provisional"] is True, "the latest day is today's running table"

    def test_the_module_never_reads_the_news_table_itself(self):
        """Events arrive only through news_logic (confirmed, non-redundant, labelled): this module cannot sum an unconfirmed lead."""
        src = open(os.path.join(_ROOT, "api", "routers", "insights_logic.py"), encoding="utf-8").read()
        assert "liquidity_events" not in src
