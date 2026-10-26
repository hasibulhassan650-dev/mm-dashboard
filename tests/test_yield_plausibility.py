"""A sub-1% bill yield is a parse error only if BB's own price disagrees.

Refetching 2016-2019 history put twelve real 2018 yields (0.57% - 0.99%) in front of
a rule that called every sub-1% value outside the COVID window "likely parse error".
They were real: BB prints a weighted-average price on the same row, and 99.78 on a
91-day bill is a yield of about 0.9%. The rule now asks whether the two agree, so it
still catches the thing it was written for - a misread column - without crying wolf
on history.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate

D = datetime.date


def _yields(monkeypatch, rows):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for (ad, tenor, cut, price) in rows:
        s.add(dbmod.PrimaryYieldSnapshot(
            snapshot_date=ad, auction_date=ad, issue_date=ad + datetime.timedelta(days=1),
            security_type="T_BILL" if tenor.endswith("D") else "T_BOND", tenor_label=tenor,
            cutoff_yield_pct=cut, weighted_avg_price=price, source="t"))
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    rep = validate.integrity_check(limit_per_rule=500)
    return [i for i in rep["issues"] if i.startswith("yields") and "sub-1%" in i]


def test_a_real_2018_low_yield_confirmed_by_its_own_price_is_not_flagged(monkeypatch):
    # 20-May-2018, 91D: cut-off 0.99%, weighted price 99.7835 (implies ~0.87%)
    assert _yields(monkeypatch, [(D(2018, 5, 20), "91D", 0.99, 99.7835)]) == []


def test_a_misread_column_is_still_caught(monkeypatch):
    """0.99 beside a price of 99.26 (which is a 3.0% yield) is exactly the parse error."""
    hits = _yields(monkeypatch, [(D(2018, 5, 20), "91D", 0.99, 99.2586)])
    assert len(hits) == 1 and "2018-05-20" in hits[0]


def test_a_low_yield_with_no_price_keeps_the_old_suspicion(monkeypatch):
    assert len(_yields(monkeypatch, [(D(2018, 5, 20), "91D", 0.99, None)])) == 1


def test_a_bond_below_1pct_is_still_flagged_even_with_a_price(monkeypatch):
    assert len(_yields(monkeypatch, [(D(2018, 5, 20), "10Y", 0.5, 99.9)])) == 1


def test_the_covid_window_is_untouched(monkeypatch):
    assert _yields(monkeypatch, [(D(2021, 3, 7), "91D", 0.6, None)]) == []


def test_each_tenor_uses_its_own_day_count(monkeypatch):
    # 364D at price 99.40 -> (100/99.4-1)*365/364*100 = 0.61%: consistent with 0.7
    assert _yields(monkeypatch, [(D(2018, 5, 20), "364D", 0.7, 99.40)]) == []
    # the same price on a 91D bill would be 2.4% - disagrees with 0.7
    assert len(_yields(monkeypatch, [(D(2018, 5, 20), "91D", 0.7, 99.40)])) == 1
