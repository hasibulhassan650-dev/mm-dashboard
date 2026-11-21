"""One BB reference-rate print must never be stored under two dates (Mar-Apr 2026: four dates, DOMMR 11.0 against a 9.8 market)."""
import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate

NOW = datetime.datetime(2026, 11, 21)


def _check(monkeypatch, dates):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for d in dates:
        for t, p, amt, deals in (("DOMMR", "Overnight", 3986.0, 42), ("DOMMR", "1W", 3728.94, 39), ("BOFR", "Overnight", 4610.55, 33)):
            s.add(dbmod.RefRate(trade_date=d, rate_type=t, product=p, amount_crore=amt, rate_pct=9.5, num_deals=deals, ingested_utc=NOW))
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    return [i for i in validate.integrity_check()["issues"] if i.startswith("ref_rates")]


def test_the_same_block_on_two_dates_is_a_defect(monkeypatch):
    got = _check(monkeypatch, [datetime.date(2026, 4, 1), datetime.date(2026, 4, 2)])
    assert len(got) == 1 and "2026-04-02" in got[0] and "2026-04-01" in got[0]


def test_one_print_per_date_is_quiet(monkeypatch):
    assert _check(monkeypatch, [datetime.date(2026, 4, 1)]) == []
