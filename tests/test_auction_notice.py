"""An auction notice states what BB is actually OFFERING; the calendar's planned amount must agree or the ladder's outflow is wrong.

The PDF is in the legacy Bijoy font: words are noise, figures and the English title are intact. Real notices of 22-Nov-2026 are the fixture."""
import datetime
import json
import pathlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate
from fetchers.auction_notice import parse_auction_notice, tenor_class

D = datetime.date
NOTICES = json.loads((pathlib.Path(__file__).parent / "fixtures" / "auction_notices.json").read_text(encoding="utf-8"))
BY = {n["serial"]: n for n in NOTICES}


def test_the_real_notices_are_read_exactly():
    a = parse_auction_notice(BY["pr14401"]["title"], BY["pr14401"]["body"])
    assert a == {"auction_date": D(2026, 11, 24), "face_crore": 2750.0, "isin": "BD0945081208", "coupon_pct": 10.3,
                 "remaining_years": 18.75, "tenor_label": "20Y"}
    b = parse_auction_notice(BY["pr14400"]["title"], BY["pr14400"]["body"])
    assert b["tenor_label"] == "15Y" and b["isin"] == "BD0942241201" and b["coupon_pct"] == 8.95   # same day, two re-issues, two classes


def test_a_re_issue_is_classed_by_its_remaining_life_not_its_original_tenor():
    """'20 years (Re-Issue for Remaining 16.09 Years)' is the 15Y bucket on the calendar; keying on '20 years' would miss it."""
    assert [tenor_class(x, False) for x in (2.05, 5.86, 10.61, 16.09, 18.75)] == ["2Y", "5Y", "10Y", "15Y", "20Y"]
    assert tenor_class(2.58, True) == "3Y_FRTB"


def test_a_notice_without_a_face_value_is_not_guessed():
    assert parse_auction_notice(BY["pr14401"]["title"], "no figures here") is None
    assert parse_auction_notice("Auction Notice without a date", BY["pr14401"]["body"]) is None


def _check(monkeypatch, calendar_offered):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    n = BY["pr14401"]
    s.add(dbmod.PressRelease(serial=n["serial"], published_date=D.today(), title=n["title"], url="u", category="AUCTION_NOTICE",
                             body_text=n["body"], first_seen_utc=datetime.datetime.utcnow(), last_seen_utc=datetime.datetime.utcnow()))
    if calendar_offered is not None:
        s.add(dbmod.AuctionEvent(fiscal_year="2026-27", auction_date=D(2026, 11, 24), settlement_date=D(2026, 11, 25), security_type="T_BOND",
                                 tenor_label="20Y", offered_amount_bdt_crore=calendar_offered, offered_amount_bdt_mill=calendar_offered * 10))
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    v = validate.integrity_check()
    return [x for x in v["notices"] if x.startswith("auction-notice")], v


def test_an_amount_that_differs_from_the_calendar_is_noticed_and_a_match_is_quiet(monkeypatch):
    assert _check(monkeypatch, 2750.0)[0] == []
    got, v = _check(monkeypatch, 2000.0)
    assert len(got) == 1 and "2,750" in got[0] and "2,000" in got[0]
    assert not [i for i in v["issues"] if i.startswith("auction-notice")], "a notice never fails the build"


def test_an_auction_the_calendar_does_not_have_is_noticed(monkeypatch):
    got, _ = _check(monkeypatch, None)
    assert len(got) == 1 and "no such auction" in got[0]
