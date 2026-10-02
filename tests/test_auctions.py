"""The auction book.

Auction data was served by an endpoint nobody called, and the endpoint could not
see PLANNED auctions at all -- it filtered `auction_date >= since` with no
forward reach, so BB's published calendar was invisible to the one route meant
to list auctions. These tests pin the three things that made it wrong:

  * the window must reach FORWARD, or the next auctions do not appear;
  * a PLANNED auction must be listed and marked, never dropped for having no
    results yet and never summed as if it had settled;
  * "notified" (BB's offer) and "bids received" are different numbers from
    different BB pages and must not collapse into one "offered".

The join is on SETTLEMENT date, not auction date, because the calendar and the
results legitimately disagree about the auction date -- the 23-May-2026 bills
being the case that proved it.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import AuctionEvent, PrimaryYieldSnapshot

sys.path.append(os.path.join(_ROOT, "api", "routers"))
from auction_logic import auction_book, bid_to_cover, resolve_window  # noqa: E402

D = datetime.date
TODAY = D(2026, 10, 3)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _auc(s, adate, settle, tenor, notified, accepted=None, stype="T_BILL",
         status="CONFIRMED", war=None, no="1"):
    s.add(AuctionEvent(fiscal_year="2026-27", auction_no=no, auction_date=adate,
                       settlement_date=settle, security_type=stype, tenor_label=tenor,
                       offered_amount_bdt_crore=notified,
                       offered_amount_bdt_mill=notified * 10,
                       accepted_amount_bdt_crore=accepted,
                       accepted_amount_bdt_mill=accepted * 10 if accepted else None,
                       weighted_avg_yield_pct=war, outflow_status=status))


def _res(s, adate, issue, tenor, bids, accepted, cutoff):
    s.add(PrimaryYieldSnapshot(snapshot_date=issue, auction_date=adate, issue_date=issue,
                               security_type="T_BILL", tenor_label=tenor, tenor_years=0.25,
                               cutoff_yield_pct=cutoff, offered_bdt_crore=bids,
                               accepted_bdt_crore=accepted))


def _one(p, tenor):
    return next(a for a in p["auctions"] if a["tenor_label"] == tenor)


class TestTheWindowReachesForward:
    """The old query was backward-only, so the auctions a desk must prepare for
    were the exact ones it could not show."""

    def test_the_default_window_runs_to_the_end_of_bbs_calendar(self):
        eng, s = _mem()
        _auc(s, D(2026, 12, 27), D(2026, 12, 28), "91D", 5000.0, status="PLANNED")
        s.commit()
        f, t = resolve_window(s, today=TODAY)
        assert t >= D(2026, 12, 28), t
        assert f < TODAY

    def test_a_planned_future_auction_is_in_the_default_window(self):
        eng, s = _mem()
        _auc(s, D(2026, 11, 1), D(2026, 11, 2), "91D", 5000.0, status="PLANNED")
        s.commit()
        p = auction_book(s, today=TODAY)
        assert _one(p, "91D")["status"] == "PLANNED"
        assert p["calendar_published_to"] == "2026-11-02"

    def test_an_explicit_range_is_honoured_and_a_reversed_one_refused(self):
        eng, s = _mem()
        _auc(s, D(2026, 5, 23), D(2026, 5, 24), "91D", 3500.0, 3500.0)
        s.commit()
        p = auction_book(s, date_from="2026-05-01", date_to="2026-05-31", today=TODAY)
        assert p["count"] == 1
        with pytest.raises(ValueError):
            auction_book(s, date_from="2026-06-01", date_to="2026-05-01", today=TODAY)

    def test_months_still_works(self):
        eng, s = _mem()
        _auc(s, D(2026, 9, 20), D(2026, 9, 21), "91D", 5000.0, 5000.0)
        _auc(s, D(2024, 1, 10), D(2024, 1, 11), "91D", 1000.0, 1000.0, no="2")
        s.commit()
        p = auction_book(s, months=6, today=TODAY)
        dates = [a["auction_date"] for a in p["auctions"]]
        assert "2026-09-20" in dates and "2024-01-10" not in dates


class TestPlannedVersusConfirmed:
    def test_a_planned_auction_is_listed_with_blank_results_not_dropped(self):
        # Dropping it for having no results is how the calendar became invisible.
        eng, s = _mem()
        _auc(s, D(2026, 11, 1), D(2026, 11, 2), "182D", 3000.0, status="PLANNED")
        s.commit()
        a = _one(auction_book(s, today=TODAY), "182D")
        assert a["status"] == "PLANNED"
        assert a["notified_crore"] == 3000.0
        assert a["accepted_crore"] is None
        assert a["cutoff_yield_pct"] is None
        assert a["has_results"] is False

    def test_planned_and_confirmed_are_counted_separately(self):
        eng, s = _mem()
        _auc(s, D(2026, 9, 20), D(2026, 9, 21), "91D", 5000.0, 5000.0)
        _auc(s, D(2026, 11, 1), D(2026, 11, 2), "182D", 3000.0, status="PLANNED", no="2")
        s.commit()
        t = auction_book(s, today=TODAY)["totals"]
        assert t["confirmed"] == 1 and t["planned"] == 1 and t["auctions"] == 2


class TestNotifiedAndBidsStaySeparate:
    """Two BB pages, two different numbers, both historically called "offered"."""

    @pytest.fixture
    def seeded(self):
        eng, s = _mem()
        # notified 3,500; the market bid 6,904.22; BB accepted 3,500
        _auc(s, D(2026, 5, 24), D(2026, 5, 24), "91D", 3500.0, 3500.0, war=10.15)
        _res(s, D(2026, 5, 23), D(2026, 5, 24), "91D", 6904.22, 3500.0, 10.15)
        s.commit()
        return eng, s

    def test_both_numbers_survive_under_their_own_names(self, seeded):
        _, s = seeded
        a = _one(auction_book(s, today=TODAY), "91D")
        assert a["notified_crore"] == 3500.0
        assert a["bids_crore"] == 6904.22
        assert a["accepted_crore"] == 3500.0

    def test_bid_to_cover_uses_bids_over_accepted(self, seeded):
        _, s = seeded
        assert _one(auction_book(s, today=TODAY), "91D")["bid_to_cover"] == round(6904.22 / 3500.0, 3)

    def test_the_results_join_survives_the_calendar_and_results_disagreeing(self, seeded):
        # The calendar says the auction was 24-May; BB's results say 23-May. The
        # join is on settlement/issue date precisely so this still matches --
        # joining on auction_date would lose every moved auction.
        _, s = seeded
        a = _one(auction_book(s, today=TODAY), "91D")
        assert a["auction_date"] == "2026-05-24"      # the calendar's
        assert a["has_results"] is True               # still joined
        assert a["cutoff_yield_pct"] == 10.15


class TestBidToCover:
    def test_it_is_none_when_bids_are_unknown(self):
        assert bid_to_cover(None, 3500.0) is None

    def test_it_is_none_when_bids_equal_accepted(self):
        # The earlier era published a fixed weekly TARGET in the bids column --
        # 364D at 245 cr fully accepted, week after week. A ratio of exactly
        # 1.00 there is an artefact, and printing it would read as a genuinely
        # fully-covered auction.
        assert bid_to_cover(245.0, 245.0) is None

    def test_it_is_none_when_nothing_was_accepted(self):
        assert bid_to_cover(5000.0, 0.0) is None

    def test_a_real_ratio_is_returned(self):
        assert bid_to_cover(7000.0, 3500.0) == 2.0


class TestRollUps:
    @pytest.fixture
    def seeded(self):
        eng, s = _mem()
        _auc(s, D(2026, 9, 20), D(2026, 9, 21), "91D", 5000.0, 5000.0, war=10.10)
        _auc(s, D(2026, 9, 20), D(2026, 9, 21), "182D", 3000.0, 3000.0, war=10.40, no="1")
        _auc(s, D(2026, 9, 22), D(2026, 9, 23), "10Y", 3000.0, 3000.0,
             stype="T_BOND", war=11.00, no="B1")
        _auc(s, D(2026, 10, 18), D(2026, 10, 19), "91D", 5000.0,
             status="PLANNED", no="2")
        s.commit()
        return eng, s

    def test_per_product_counts_and_amounts_tie_to_the_rows(self, seeded):
        _, s = seeded
        p = auction_book(s, today=TODAY)
        for k, v in p["by_product"].items():
            rows = [a for a in p["auctions"] if a["product"] == k]
            assert v["auctions"] == len(rows)
            assert v["notified_crore"] == round(sum(a["notified_crore"] or 0 for a in rows), 2)
            assert v["accepted_crore"] == round(sum(a["accepted_crore"] or 0 for a in rows), 2)

    def test_the_average_cutoff_is_weighted_by_accepted_amount(self, seeded):
        # A 5,000 cr bill and a 3,000 cr bond are not equal observations of
        # "the average yield", so a plain mean would misstate it.
        _, s = seeded
        bills = auction_book(s, today=TODAY)["by_product"]["T_BILL"]
        expected = (10.10 * 5000 + 10.40 * 3000) / 8000
        assert bills["avg_cutoff_pct"] == round(expected, 4)

    def test_a_planned_auction_does_not_pollute_the_average_yield(self, seeded):
        # It has no cut-off and nothing accepted, so it must not enter the mean.
        _, s = seeded
        p = auction_book(s, today=TODAY)
        assert p["by_product"]["T_BILL"]["planned"] == 1
        assert p["by_product"]["T_BILL"]["avg_cutoff_pct"] is not None

    def test_monthly_rollup_buckets_by_settlement_month(self, seeded):
        _, s = seeded
        months = {m["month"]: m for m in auction_book(s, today=TODAY)["months"]}
        assert months["2026-09"]["auctions"] == 3
        assert months["2026-10"]["auctions"] == 1

    def test_totals_tie_to_the_rows(self, seeded):
        _, s = seeded
        p = auction_book(s, today=TODAY)
        assert p["totals"]["notified_crore"] == round(
            sum(a["notified_crore"] or 0 for a in p["auctions"]), 2)


class TestDuplicateResultsAreSurfacedNotSilentlyDoubled:
    def test_two_results_for_one_auction_do_not_multiply_the_row(self):
        # The duplicate-auction defect put two yield rows against one auction.
        # The join aggregates first, so a duplicate shows up as a flag rather
        # than doubling the amount in every total.
        eng, s = _mem()
        _auc(s, D(2026, 5, 24), D(2026, 5, 24), "91D", 3500.0, 3500.0, war=10.15)
        _res(s, D(2026, 5, 23), D(2026, 5, 24), "91D", 6904.22, 3500.0, 10.15)
        _res(s, D(2026, 5, 21), D(2026, 5, 24), "91D", 6904.22, 3500.0, 10.15)
        s.commit()
        p = auction_book(s, today=TODAY)
        assert p["count"] == 1, "the auction was multiplied by its duplicate result"
        assert _one(p, "91D")["duplicate_results"] is True


class TestTheDeploymentBoundary:
    def test_auction_logic_imports_nothing_from_the_repo_root(self):
        import ast
        import io as _io

        import auction_logic
        forbidden = {"config", "db", "engines", "calendar_utils", "validate",
                     "fetchers", "parsers"}
        src = _io.open(auction_logic.__file__, encoding="utf-8").read()
        bad = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in forbidden:
                    bad.append(node.module)
        assert not bad, f"auction_logic cannot import from the repo root: {bad}"
