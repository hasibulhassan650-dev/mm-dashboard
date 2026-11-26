"""The sukuk wait runs from the auction the release ANNOUNCES, not from the day it was published (12-Nov's notice announced a 1-Dec auction)."""
import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate

D = datetime.date


def _check(monkeypatch, today_offset_days, announced=True):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    pub = D.today() - datetime.timedelta(days=today_offset_days)          # published `today_offset_days` ago
    s.add(dbmod.PressRelease(serial="pr1", published_date=pub, subject_date=None, title="Press Release for the 2nd Short-term Sukuk Auction",
                             url="u", category="SUKUK", first_seen_utc=datetime.datetime.utcnow(), last_seen_utc=datetime.datetime.utcnow()))
    if announced:
        auction = pub + datetime.timedelta(days=19)
        s.add(dbmod.SukukAuction(source_serial="pr1", auction_date=auction, issue_date=auction + datetime.timedelta(days=1),
                                 maturity_date=auction + datetime.timedelta(days=183), tenor_days=182, face_crore=3000.0,
                                 rent_crore=112.19, rate_pct=7.5))
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    v = validate.integrity_check()
    return [x for x in v["issues"] + v["waiting"] if x.startswith("sukuk")]


def test_nothing_is_awaited_before_the_announced_auction_is_held(monkeypatch):
    assert _check(monkeypatch, 14) == []                  # published 14 days ago, auction is 5 days ahead


def test_after_the_auction_the_wait_starts_from_the_issue_date_not_the_publication(monkeypatch):
    got = _check(monkeypatch, 30)                         # issued 10 days ago: still inside BB's 21-day listing grace
    assert len(got) == 1 and "ESCALATED" not in got[0]
    late = _check(monkeypatch, 60)                        # issued 40 days ago and still unlisted: that is a defect now
    assert len(late) == 1 and "ESCALATED" in late[0]


def test_an_unannounced_release_keeps_the_old_rule(monkeypatch):
    assert _check(monkeypatch, 5, announced=False)        # no stored announcement: waits from publication as before
