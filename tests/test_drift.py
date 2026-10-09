"""A number that parses and is still wrong.

Bangladesh Bank prints money-market volumes in crore. Suppose a page starts
printing million instead — a presentational change, the kind an institution
makes without announcement. Every figure still parses as a float, every type
check passes, the row count is normal, nothing is stale, no selector moved and
no header was renamed. The structural watcher sees nothing, because nothing
about the structure changed. The series is simply out by a factor of ten, and
it stays that way until someone compares a number to BB's own page by hand.

That is why magnitude is checked at all. These tests fix both edges:

  * a unit switch IS caught, in the series that feed the cash ladder;
  * a real market move is NOT, because an alarm that fires when call money
    goes from 9% to 11% trains the desk to ignore the report, which is the
    failure this whole effort exists to undo.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import CallMoneyRate, OMOTransaction
from engines import drift

TODAY = datetime.date(2026, 10, 10)
D = datetime.timedelta


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _omo(s, start_days_ago: int, n: int, amount: float):
    """n daily OMO tranches of a given size, ending `start_days_ago` days back."""
    for i in range(n):
        d = TODAY - D(days=start_days_ago - i)
        s.add(OMOTransaction(transaction_date=d, maturity_date=d + D(days=1),
                             instrument="CM_REPO", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=amount, rate_pct=9.5,
                             direction="INJECT", source_pdf="test"))
    s.commit()


class TestAUnitSwitchAtTheSource:
    def test_crore_becoming_million_is_caught(self):
        s = _mem()
        _omo(s, 400, 120, 9_000.0)        # a year of ordinary OMO volumes
        _omo(s, 20, 15, 90_000.0)         # then the same market, printed in million
        found = drift.findings(s, TODAY)
        assert any("omo_transactions.accepted_bdt_crore" in f for f in found), found
        assert any("10x" in f or "1e+01" in f for f in found), found

    def test_the_finding_says_what_to_do_about_it(self):
        s = _mem()
        _omo(s, 400, 120, 9_000.0)
        _omo(s, 20, 15, 90_000.0)
        f = [x for x in drift.findings(s, TODAY) if "omo_transactions" in x][0]
        # Both medians and the row count behind them: enough to check BB's page
        # without re-deriving the arithmetic.
        assert "90,000" in f.replace(",", ",") or "9e+04" in f, f
        assert "crore" in f

    def test_a_percent_printed_as_a_fraction_is_caught(self):
        s = _mem()
        for i in range(120):
            d = TODAY - D(days=400 - i)
            s.add(CallMoneyRate(trade_date=d, product="CALL", maturity_days=1,
                                amount_crore=5000.0, highest_rate_pct=10.0,
                                lowest_rate_pct=9.0, average_rate_pct=9.5, num_deals=40))
        for i in range(15):
            d = TODAY - D(days=20 - i)
            s.add(CallMoneyRate(trade_date=d, product="CALL", maturity_days=1,
                                amount_crore=5000.0, highest_rate_pct=0.10,
                                lowest_rate_pct=0.09, average_rate_pct=0.095, num_deals=40))
        s.commit()
        found = drift.findings(s, TODAY)
        assert any("call_money_rates.average_rate_pct" in f for f in found), found


class TestRealMarketMovesAreNotAlarms:
    def test_call_money_from_nine_to_eleven_percent_is_silent(self):
        s = _mem()
        for i in range(120):
            d = TODAY - D(days=400 - i)
            s.add(CallMoneyRate(trade_date=d, product="CALL", maturity_days=1,
                                amount_crore=5000.0, highest_rate_pct=9.8,
                                lowest_rate_pct=8.5, average_rate_pct=9.1, num_deals=40))
        for i in range(20):
            d = TODAY - D(days=25 - i)
            s.add(CallMoneyRate(trade_date=d, product="CALL", maturity_days=1,
                                amount_crore=5000.0, highest_rate_pct=11.9,
                                lowest_rate_pct=10.2, average_rate_pct=11.2, num_deals=40))
        s.commit()
        assert drift.findings(s, TODAY) == []

    def test_a_quiet_market_halving_turnover_is_silent(self):
        s = _mem()
        _omo(s, 400, 120, 9_000.0)
        _omo(s, 20, 15, 4_300.0)
        assert [f for f in drift.findings(s, TODAY)
                if "accepted_bdt_crore" in f] == []

    def test_a_young_series_is_never_judged(self):
        """Three data points are not a distribution."""
        s = _mem()
        _omo(s, 30, 4, 9_000.0)
        _omo(s, 5, 3, 90_000.0)
        assert drift.findings(s, TODAY) == []


class TestDatesThatParseButCannotBeTrue:
    def test_an_omo_tranche_maturing_in_decades_is_caught(self):
        s = _mem()
        d = TODAY - D(days=3)
        s.add(OMOTransaction(transaction_date=d, maturity_date=datetime.date(2045, 10, 20),
                             instrument="CM_REPO", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=9000.0, rate_pct=9.5,
                             direction="INJECT", source_pdf="test"))
        s.commit()
        found = drift.findings(s, TODAY)
        assert any("maturity_date" in f and "2045" in f for f in found), found

    def test_an_ordinary_overnight_repo_is_silent(self):
        s = _mem()
        _omo(s, 10, 5, 9_000.0)
        assert [f for f in drift.findings(s, TODAY) if "maturity_date" in f] == []
