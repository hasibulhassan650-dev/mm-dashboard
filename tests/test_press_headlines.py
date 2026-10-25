"""A press release gets a one-line headline - from figures we hold, or nothing.

The risk of a "news summary" is a confident line that is wrong: it sits in front
of BB's real release and gets believed. So each test pins a place where a
plausible shortcut would state something false.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import db as dbmod
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "routers"))
import press_logic as P  # noqa: E402  (appended, so the root db module wins over api/db.py)

TODAY = datetime.date.today()
D = datetime.timedelta


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)(), eng


def _rel(serial, category, title, pub=None, sub=None):
    return {"serial": serial, "category": category, "title": title,
            "published_date": pub or TODAY, "subject_date": sub}


def _bgiib(s, serial, tenor, months, acc, epr, day=0):
    s.add(dbmod.BgiibAuction(auction_date=TODAY - D(day), tenor_label=tenor, tenor_months=months,
                             accepted_crore=acc, offered_crore=acc, war_epr_pct=epr,
                             source_serial=serial))
    s.commit()


def test_a_bgiib_headline_is_bbs_own_figures_by_serial():
    s, _ = _mem()
    _bgiib(s, "pr1", "3M", 3, 670.0, 7.4)
    _bgiib(s, "pr1", "6M", 6, 313.04, 6.9)
    _bgiib(s, "pr2", "3M", 3, 999.0, 1.0, day=7)             # another release: must not leak
    h = P.build_headlines(s, [_rel("pr1", "BGIIB", "Islamic window result")])["pr1"]
    assert h["headline_basis"] == "table"
    assert h["headline"] == "3M 670.00 cr accepted at 7.40% EPR · 6M 313.04 cr accepted at 6.90% EPR"


def test_a_bgiib_release_with_nothing_stored_says_so_not_a_guess():
    s, _ = _mem()
    h = P.build_headlines(s, [_rel("pr1", "BGIIB", "Islamic window result")])["pr1"]
    assert h["headline_basis"] == "not_parsed" and "Not yet parsed" in h["headline"]


def test_a_buyback_result_states_face_and_that_cash_is_unpublished():
    """Accepted is FACE; cash was never published. Printing face as 'paid' would
    understate what reached the market."""
    s, _ = _mem()
    s.add(dbmod.BuybackEvent(isin="BD0926191026", announced_date=TODAY - D(3),
                             auction_date=TODAY - D(3), settlement_date=TODAY + D(1),
                             bids_received_count=56, offered_face_crore=2016.14,
                             accepted_face_crore=1716.14, cash_paid_crore=None))
    s.commit()
    h = P.build_headlines(s, [_rel("pr9", "BUYBACK", "Buyback result", pub=TODAY - D(2))])["pr9"]
    assert h["headline_basis"] == "table"
    assert "1,716.14 cr face of 2,016.14 cr offered" in h["headline"]
    assert "56 bids" in h["headline"] and "cash paid not published" in h["headline"]


def test_a_buyback_notice_is_never_linked_to_an_event():
    """A notice announces a coming operation; an event is a completed one."""
    s, _ = _mem()
    s.add(dbmod.BuybackEvent(isin="X", announced_date=TODAY, auction_date=TODAY,
                             settlement_date=TODAY + D(2), accepted_face_crore=1.0))
    s.commit()
    h = P.build_headlines(s, [_rel("pr9", "BUYBACK",
                                   "Notice of buyback of 2 year bond")])["pr9"]
    assert h["headline_basis"] == "title"


def test_two_buyback_events_in_the_window_are_ambiguous_not_picked():
    s, _ = _mem()
    for i in (1, 2):
        s.add(dbmod.BuybackEvent(isin=f"X{i}", announced_date=TODAY, auction_date=TODAY,
                                 settlement_date=TODAY + D(i), accepted_face_crore=1.0))
    s.commit()
    h = P.build_headlines(s, [_rel("pr9", "BUYBACK", "Buyback result")])["pr9"]
    assert h["headline_basis"] == "ambiguous"


def _yield(s, auction, issue, tenor, cutoff, acc, sale):
    s.add(dbmod.PrimaryYieldSnapshot(
        snapshot_date=auction, security_type="T_BOND", tenor_label=tenor, original_tenor_label=tenor,
        original_tenor_years=float(tenor.rstrip("Y")), auction_date=auction,
        issue_date=issue, cutoff_yield_pct=cutoff, accepted_bdt_crore=acc,
        sale_value_bdt_crore=sale, source="t"))
    s.commit()


def test_a_result_links_on_the_auction_date_not_the_issue_date():
    """'held on' is the day bids were taken. BB prints the NEXT working day as the
    issue date; joining on that attaches Sunday's figures to Monday's release."""
    s, _ = _mem()
    a = TODAY - D(5)
    _yield(s, a, a + D(1), "10Y", 12.5, 3000.0, 2900.0)
    _yield(s, a + D(1), a + D(2), "5Y", 9.0, 1.0, 1.0)         # a decoy on the issue date
    h = P.build_headlines(s, [_rel("pr5", "TBOND_RESULT", "10-Year result",
                                   pub=a + D(1), sub=a)])["pr5"]
    assert "10Y" in h["headline"] and "5Y" not in h["headline"]
    assert "cut-off 12.5000%" in h["headline"]


def test_a_per_tenor_release_shows_only_its_own_tenor():
    """BB posts one release per tenor on the same day; both showing the same
    two-tenor line would attribute the 3Y figures to the 10Y release."""
    s, _ = _mem()
    a = TODAY - D(5)
    _yield(s, a, a + D(1), "10Y", 12.5, 3000.0, 2900.0)
    _yield(s, a, a + D(1), "5Y", 9.0, 800.0, 810.0)
    h = P.build_headlines(s, [_rel("pr5", "TBOND_RESULT", "10-Year Bangladesh Govt. Treasury "
                                   "Bond Auction Result", pub=a + D(1), sub=a)])["pr5"]
    assert "10Y" in h["headline"] and "5Y" not in h["headline"]


def test_a_blank_sale_value_is_said_not_replaced_by_face():
    s, _ = _mem()
    a = TODAY - D(5)
    _yield(s, a, a + D(1), "10Y", 12.5, 3000.0, None)
    h = P.build_headlines(s, [_rel("pr5", "TBOND_RESULT", "result", pub=a + D(1), sub=a)])["pr5"]
    assert "sale value not published" in h["headline"]
    assert "sale value 3,000" not in h["headline"]


def test_a_result_with_no_stored_auction_says_not_parsed():
    s, _ = _mem()
    h = P.build_headlines(s, [_rel("pr5", "TBILL_RESULT", "result", sub=TODAY - D(3))])["pr5"]
    assert h["headline_basis"] == "not_parsed"


def test_a_notice_headline_states_only_what_the_title_states():
    s, _ = _mem()
    when = TODAY + D(2)
    h = P.build_headlines(s, [_rel(
        "pr7", "AUCTION_NOTICE",
        "Auction Notice of 10 Years (Re-Issue for Remaining 5.61 Years) BGTB, 8.0% Coupon",
        sub=when)])["pr7"]
    assert h["headline_basis"] == "title"
    assert "10-year" in h["headline"] and "re-issue" in h["headline"]
    assert "8.0% coupon" in h["headline"] and when.strftime("%d-%b-%Y") in h["headline"]


def test_an_unrecognised_title_gets_no_invented_headline():
    s, _ = _mem()
    assert "pr1" not in P.build_headlines(s, [_rel("pr1", "AUCTION_NOTICE", "Circular")])
    assert "pr2" not in P.build_headlines(s, [_rel("pr2", "POLICY", "Policy rate")])


def test_omo_states_dealt_amounts_and_does_not_restate_the_net():
    """A tranche moves liquidity twice (deal and maturity); the net belongs to the
    ladder. Summing deals alone and calling it the effect is the sign trap."""
    s, _ = _mem()
    d = TODAY - D(1)
    s.add(dbmod.OMOTransaction(transaction_date=d, maturity_date=d + D(7), instrument="SDF",
                               tenor_label="7D", tenor_days=7, accepted_bdt_crore=100.0,
                               rate_pct=10.0, direction="ABSORPTION", source_pdf="x"))
    s.add(dbmod.OMOTransaction(transaction_date=d, maturity_date=d + D(1), instrument="SLF",
                               tenor_label="1D", tenor_days=1, accepted_bdt_crore=40.0,
                               rate_pct=11.0, direction="INJECTION", source_pdf="x"))
    s.commit()
    h = P.build_headlines(s, [_rel("pr3", "OMO", "OMO", pub=TODAY, sub=d)])["pr3"]
    assert "Injected 40.00 cr" in h["headline"] and "absorbed 100.00 cr" in h["headline"]
    assert "see Cash Flows" in h["headline"]


def test_string_dates_from_sqlite_are_coerced_not_dropped():
    assert P._d("2026-10-12") == datetime.date(2026, 10, 12)
    assert P._d(datetime.datetime(2026, 10, 12, 5)) == datetime.date(2026, 10, 12)
    assert P._d("garbage") is None


def test_the_list_costs_a_constant_number_of_queries():
    """60 releases must not mean 60 round trips - this endpoint is hit per page view."""
    def count(n):
        s, eng = _mem()
        seen = []
        event.listen(eng, "before_cursor_execute", lambda *a, **k: seen.append(1))
        rels = [_rel(f"pr{i}", c, "Notice 10 Years", sub=TODAY - D(i % 9))
                for i, c in zip(range(n), ["BGIIB", "BUYBACK", "TBILL_RESULT", "OMO", "AUCTION_NOTICE"] * 20)]
        P.build_headlines(s, rels)
        return len(seen)
    assert count(5) == count(60) <= 5


def test_press_logic_respects_the_api_deploy_boundary():
    """api/ deploys rooted at api/: db, engines and fetchers are not there."""
    import ast
    src = open(os.path.join(os.path.dirname(P.__file__), "press_logic.py"), encoding="utf-8").read()
    mods = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.module else a.name.split(".")[0]
            for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in getattr(n, "names", [None])}
    assert not mods & {"db", "engines", "fetchers", "config"}
