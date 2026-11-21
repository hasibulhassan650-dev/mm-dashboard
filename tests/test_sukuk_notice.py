"""A sukuk auction is announced in BB's press release days to weeks before BB's sukuk page lists the issue.

The release is in the legacy Bijoy font, so its Bangla is noise; the figures are not. The parser must read them exactly,
must check BB's own total rent against face x rate x days/365, and must REFUSE (never half-read) when anything disagrees."""
import datetime
import pathlib
import re

import pytest
from fetchers.sukuk_notice import parse_sukuk_notice

D = datetime.date
TEXT = (pathlib.Path(__file__).parent / "fixtures" / "sukuk_notice_pr14384.txt").read_text(encoding="utf-8")
TITLE = "Press Release for the 2nd Short-term Bangladesh Government Investment Sukuk Auction"


def test_the_real_12_nov_release_is_read_exactly():
    r = parse_sukuk_notice(TEXT, "pr14384", TITLE, "u")
    assert r["refused"] is None
    assert r["row"] == {"source_serial": "pr14384", "source_url": "u", "auction_date": D(2026, 12, 1),
                        "issue_date": D(2026, 12, 2), "maturity_date": D(2027, 6, 2), "tenor_days": 182,
                        "face_crore": 3000.0, "rent_crore": 112.19, "rate_pct": 7.5,
                        "name": "Short Term BGIS (182 Days)", "series_no": 2}


@pytest.mark.parametrize("old,new,why", [
    ("112.19", "121.19", "a transposed digit in BB's own total rent must not be stored"),
    ("7.50%", "7.05%", "the annual rate disagreeing with the total rent"),
    ("(182 Days)", "(183 Days)", "a tenor that does not reach the printed maturity"),
    ("Ryb, 2027", "Rvbyqvwi, 2027", "a maturity that is not tenor days after the auction"),
    ("Ryb, 2027", "Xyz, 2027", "a month word we do not know is refused, not guessed"),
])
def test_a_release_whose_figures_disagree_is_refused_whole(old, new, why):
    assert old in TEXT
    r = parse_sukuk_notice(TEXT.replace(old, new, 1), "pr14384", TITLE)
    assert r["row"] is None and r["refused"], why


def test_a_different_kind_of_release_is_refused_not_forced_into_the_shape():
    assert parse_sukuk_notice("Press release on something else", "pr1")["row"] is None


def test_the_api_shows_where_each_announcement_stands():
    import importlib.util
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db as dbmod
    spec = importlib.util.spec_from_file_location(
        "sukuk_logic_under_test", pathlib.Path(__file__).resolve().parents[1] / "api" / "routers" / "sukuk_logic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)             # by path: putting api/ on sys.path would shadow the root db module
    announced_auctions = mod.announced_auctions
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    row = parse_sukuk_notice(TEXT, "pr14384", TITLE, "u")["row"]
    s.add(dbmod.SukukAuction(**row)); s.commit()
    assert announced_auctions(s, [], D(2026, 11, 21))[0]["status"] == "ANNOUNCED"
    assert announced_auctions(s, [], D(2026, 12, 5))[0]["status"] == "HELD"          # BB has not listed it yet
    listed = [{"maturity_date": "2027-06-02"}]
    assert announced_auctions(s, listed, D(2026, 12, 20))[0]["status"] == "LISTED"
    s.execute(dbmod.text("DROP TABLE sukuk_auctions")); s.commit()
    assert announced_auctions(s, [], D(2026, 11, 21)) == []                         # table not created yet: nothing shown
