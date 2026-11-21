"""A day the market traded with no OMO operation held and no recorded absence must be noticed.

31-Mar-2026: call money printed 7 rows, BB's OMO release was never held, and nothing said so - the ledger audit only walks
days BB printed something on, so a day we never received is invisible to it."""
import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate

NOW = datetime.datetime(2026, 11, 21, 12, 0)


def _check(monkeypatch, traded, omo=(), absent=()):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for d in traded:
        s.add(dbmod.CallMoneyRate(trade_date=d, product="Overnight", maturity_days=1, amount_crore=1.0, ingested_utc=NOW))
    for d in omo:
        s.add(dbmod.OMOTransaction(transaction_date=d, maturity_date=d, instrument="SDF", tenor_label="1D", tenor_days=1,
                                   accepted_bdt_crore=1.0, rate_pct=8.0, direction="ABSORPTION", ingested_utc=NOW))
    for d in absent:
        s.add(dbmod.DataAbsence(dataset="omo", absent_date=d, kind="bb_omission", reason="x", evidence="x")) if hasattr(dbmod, "DataAbsence") else None
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    v = validate.integrity_check()
    return [n for n in v["notices"] if n.startswith("omo-coverage")], v


def test_a_traded_day_with_no_omo_is_noticed_not_failed(monkeypatch):
    day = datetime.date.today() - datetime.timedelta(days=20)
    start = day - datetime.timedelta(days=30)                    # OMO history began earlier; this day is a hole inside it
    got, v = _check(monkeypatch, [day], omo=[start])
    assert len(got) == 1 and str(day) in got[0]
    assert not [i for i in v["issues"] if i.startswith("omo-coverage")]


def test_nothing_is_owed_from_before_omo_history_begins(monkeypatch):
    day = datetime.date.today() - datetime.timedelta(days=20)
    assert _check(monkeypatch, [day], omo=[day + datetime.timedelta(days=5)])[0] == []


def test_a_day_with_omo_held_or_no_trading_is_quiet(monkeypatch):
    day = datetime.date.today() - datetime.timedelta(days=20)
    assert _check(monkeypatch, [day], omo=[day])[0] == []
    assert _check(monkeypatch, [])[0] == []                      # a closure: call money printed nothing, so nothing is owed
