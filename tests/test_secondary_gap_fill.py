"""
Secondary-market (GSOM) snapshots for a working day that was missed are re-read from BB's date picker.

On 25-27 Oct 2026 the refresh could not read BB (its firewall blocked the runners), so three working days had
no snapshots; BB still served them and they were restored by hand. These tests fix the rules that make that
automatic AND safe: only real holes inside history, only working days, only rows BB itself labels with that
day, only securities we know - so a gap fill can never invent or mis-date a figure.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import calendar_utils
import db as dbmod
from db import MtmSnapshot, Security
from engines import pipeline as P

D = datetime.date
TODAY = D(2026, 10, 29)          # Thursday; Fri/Sat are the weekend, so 25-28 Oct are working days


@pytest.fixture(autouse=True)
def calendar():
    saved = (calendar_utils._holiday_set, calendar_utils._working_weekend_set)
    calendar_utils.load_holidays(set(), set())
    yield
    calendar_utils._holiday_set, calendar_utils._working_weekend_set = saved


@pytest.fixture
def parsed(monkeypatch):
    """Stand in for the HTML parsers: the fake page text IS 'isin|label-date;isin|label-date'."""
    import parsers.securities as S

    def parse(html, url, *a):
        out = []
        for item in html.split(";"):
            isin, label = item.split("|")
            out.append({"isin": isin, "settlement_date": D.fromisoformat(label), "market_yield_pct": 9.0,
                        "market_price": 100.0})
        return out
    monkeypatch.setattr(S, "parse_tbond_or_frtb", parse)
    monkeypatch.setattr(S, "parse_tbill", lambda html, url: parse(html, url))
    monkeypatch.setattr(S, "validate_rows", lambda rows: rows)


def _db():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(Security(isin="BD0000000001"))
    s.add(MtmSnapshot(isin="BD0000000001", settlement_date=D(2026, 10, 22), market_yield_pct=9.0))
    s.add(MtmSnapshot(isin="BD0000000001", settlement_date=D(2026, 10, 28), market_yield_pct=9.1))
    s.commit()
    return s


def _page_for(requested, label=None, isin="BD0000000001"):
    return f"{isin}|{(label or requested).isoformat()}"


def _fetch(label_override=None, isin="BD0000000001", empty_for=()):
    calls = []

    def fetch(stype, d):
        calls.append((stype, d))
        return "" if d in empty_for else _page_for(d, label_override, isin)
    fetch.calls = calls
    return fetch


def _days(s):
    return sorted({d for (d,) in s.query(MtmSnapshot.settlement_date).all()})


def test_missed_working_days_are_filled_but_not_weekends_or_today(parsed):
    s = _db()
    out = P.backfill_secondary_gaps(s, today=TODAY, fetch_page=_fetch())
    assert sorted(out["filled"]) == ["2026-10-25", "2026-10-26", "2026-10-27"]
    assert D(2026, 10, 23) not in _days(s) and D(2026, 10, 24) not in _days(s)      # Fri / Sat
    assert TODAY not in _days(s)                                                      # today's own fetch owns it


def test_a_page_labelled_with_another_day_is_never_stored_under_the_requested_day(parsed):
    """If BB's picker ignored the request and returned the 28th's table, storing it as the 26th would put a
    wrong yield curve into a day that never had it."""
    s = _db()
    out = P.backfill_secondary_gaps(s, today=TODAY, fetch_page=_fetch(label_override=D(2026, 10, 28)))
    assert out["filled"] == {} and _days(s) == [D(2026, 10, 22), D(2026, 10, 28)]


def test_a_security_outside_the_master_is_not_stored(parsed):
    s = _db()
    out = P.backfill_secondary_gaps(s, today=TODAY, fetch_page=_fetch(isin="BD9999999999"))
    assert out["filled"] == {}


def test_history_before_the_first_snapshot_is_not_extended(parsed):
    s = _db()
    f = _fetch()
    P.backfill_secondary_gaps(s, today=TODAY, fetch_page=f)
    assert all(d > D(2026, 10, 22) for _, d in f.calls)


def test_a_second_run_asks_for_nothing(parsed):
    s = _db()
    P.backfill_secondary_gaps(s, today=TODAY, fetch_page=_fetch())
    f = _fetch()
    again = P.backfill_secondary_gaps(s, today=TODAY, fetch_page=f)
    assert again["filled"] == {} and f.calls == []


def test_a_day_bb_has_no_table_for_is_left_empty_without_an_error(parsed):
    s = _db()
    out = P.backfill_secondary_gaps(s, today=TODAY, fetch_page=_fetch(empty_for={D(2026, 10, 26)}))
    assert sorted(out["filled"]) == ["2026-10-25", "2026-10-27"] and out["errors"] == []


def test_a_failing_fetch_is_reported_and_the_other_days_still_run(parsed):
    s = _db()

    def flaky(stype, d):
        if d == D(2026, 10, 25):
            raise RuntimeError("503")
        return _page_for(d)
    out = P.backfill_secondary_gaps(s, today=TODAY, fetch_page=flaky)
    assert any("2026-10-25" in e for e in out["errors"]) and sorted(out["filled"]) == ["2026-10-26", "2026-10-27"]
