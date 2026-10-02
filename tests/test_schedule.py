"""The forward schedule, split by product.

The report could not previously answer "how much T-Bond principal matures in
March" — daily_net_flow carries only an aggregate coupon and principal figure,
and is maintained over a rolling window while the events run to 2045. These
tests pin the two things that make the product split trustworthy rather than
merely present:

  * a T-Bill coupon of zero is a FACT about zero-coupon discount instruments,
    not an empty cell, and it must survive into the totals;
  * past the end of BB's published auction calendar there is no auction figure,
    and zero must never stand in for it. A stale calendar shown as zero told
    this desk "no auctions next month" as if it were fact, once already.
"""
import datetime
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import AuctionEvent, CouponEvent, MaturityEvent, Security

# The schedule logic lives in api/routers/ because the deployed API is rooted at
# api/ and cannot import anything above it. Appended (not inserted) so repo-root
# modules still win -- api/db.py must never shadow the real db module here.
sys.path.append(os.path.join(_ROOT, "api", "routers"))
from schedule_logic import event_detail, monthly_by_product  # noqa: E402

D = datetime.date
TODAY = D(2026, 10, 15)          # fixed so the window never moves under the tests


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _sec(s, isin, stype, name="x"):
    s.add(Security(isin=isin, security_type=stype, security_name_norm=name,
                   outstanding_bdt_mill=1000.0))


def _mat(s, isin, pay, mill):
    s.add(MaturityEvent(isin=isin, scheduled_date=pay, payment_date=pay,
                        principal_bdt_mill=mill))


def _coup(s, isin, pay, mill, rate=10.0):
    s.add(CouponEvent(isin=isin, scheduled_date=pay, payment_date=pay,
                      amount_bdt_mill=mill, coupon_rate_used_pct=rate))


def _auc(s, settle, stype, mill, tenor="91D", status="CONFIRMED"):
    s.add(AuctionEvent(fiscal_year="2026-27", auction_date=settle - datetime.timedelta(days=1),
                       settlement_date=settle, security_type=stype, tenor_label=tenor,
                       offered_amount_bdt_mill=mill, accepted_amount_bdt_mill=mill,
                       outflow_status=status, weighted_avg_yield_pct=10.0))


def _month(p, ym):
    return next(m for m in p["months"] if m["month"] == ym)


@pytest.fixture
def seeded():
    """One of each product, spread over two months and two fiscal years."""
    eng, s = _mem()
    _sec(s, "BD0BOND00001", "T_BOND", "10Y BGTB")
    _sec(s, "BD0BILL00001", "T_BILL", "91D T-BILL")
    _sec(s, "BD0FRTB00001", "FRTB", "3Y FRTB")
    # October 2026 — FY 2026-27
    _mat(s, "BD0BOND00001", D(2026, 10, 20), 5000.0)      # 500 cr
    _mat(s, "BD0BILL00001", D(2026, 10, 22), 30000.0)     # 3,000 cr
    _coup(s, "BD0BOND00001", D(2026, 10, 20), 600.0)      # 60 cr
    _coup(s, "BD0FRTB00001", D(2026, 10, 25), 150.0)      # 15 cr
    _auc(s, D(2026, 10, 18), "T_BILL", 35000.0)           # 3,500 cr
    # July 2027 — FY 2027-28
    _mat(s, "BD0FRTB00001", D(2027, 7, 3), 2000.0)        # 200 cr
    s.commit()
    return eng, s


class TestProductSegregation:
    def test_each_product_lands_in_its_own_bucket(self, seeded):
        _, s = seeded
        oct26 = _month(monthly_by_product(s, 2, TODAY), "2026-10")
        assert oct26["redemption"]["T_BOND"] == 500.0
        assert oct26["redemption"]["T_BILL"] == 3000.0
        assert oct26["redemption"]["FRTB"] == 0.0
        assert oct26["coupon"]["T_BOND"] == 60.0
        assert oct26["coupon"]["FRTB"] == 15.0
        assert oct26["auction"]["T_BILL"] == 3500.0

    def test_a_total_is_the_sum_of_its_products(self, seeded):
        # If a product is ever dropped from the split, the total must stop
        # agreeing — that is what makes this test able to fail.
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        for m in p["months"]:
            for kind in ("redemption", "coupon", "auction"):
                parts = sum(m[kind][k] for k in p["products"])
                assert round(parts, 2) == m[kind]["total"], (m["month"], kind)

    def test_months_are_separated_not_pooled(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        assert _month(p, "2027-07")["redemption"]["FRTB"] == 200.0
        assert _month(p, "2026-10")["redemption"]["FRTB"] == 0.0

    def test_an_unmatched_isin_surfaces_as_other_and_stays_in_the_total(self):
        # An event whose ISIN has no securities row must not vanish from the
        # month's total just because its product is unknown.
        eng, s = _mem()
        _mat(s, "BD0NOSUCH001", D(2026, 10, 9), 7000.0)
        s.commit()
        oct26 = _month(monthly_by_product(s, 1, TODAY), "2026-10")
        assert oct26["redemption"]["OTHER"] == 700.0
        assert oct26["redemption"]["total"] == 700.0


class TestBillsHaveNoCoupon:
    def test_tbill_coupon_is_zero_not_missing(self, seeded):
        # Bills are zero-coupon discount instruments. The figure is 0.0 and the
        # payload names which products can carry a coupon, so a caller never has
        # to infer that an empty cell means "no data".
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        oct26 = _month(p, "2026-10")
        assert oct26["coupon"]["T_BILL"] == 0.0
        assert "T_BILL" not in p["coupon_products"]
        assert p["coupon_products"] == ["T_BOND", "FRTB"]

    def test_a_bill_only_month_still_totals_correctly(self):
        eng, s = _mem()
        _sec(s, "BD0BILL00001", "T_BILL")
        _mat(s, "BD0BILL00001", D(2026, 11, 5), 10000.0)
        s.commit()
        nov = _month(monthly_by_product(s, 1, TODAY), "2026-11")
        assert nov["coupon"]["total"] == 0.0
        assert nov["redemption"]["total"] == 1000.0
        assert nov["inflow_total"] == 1000.0


class TestAuctionsBeyondBBsCalendar:
    """The regression that matters most. BB publishes its auction calendar about
    a year out; past that we know nothing. Showing 0 would assert there is no
    auction, which is a different claim from "BB has not said yet" — and the
    second one is the true one."""

    def test_a_month_past_the_calendar_is_not_published_not_zero(self, seeded):
        _, s = seeded                      # last auction settles 2026-10-18
        p = monthly_by_product(s, 2, TODAY)
        assert p["auction_calendar_to"] == "2026-10-18"
        far = _month(p, "2027-07")
        assert far["auction"]["status"] == "not_published"

    def test_a_month_fully_inside_the_calendar_is_published(self, seeded):
        _, s = seeded
        _auc(s, D(2026, 11, 30), "T_BILL", 1000.0)      # calendar now ends 30-Nov
        s.commit()
        p = monthly_by_product(s, 2, TODAY)
        assert _month(p, "2026-10")["auction"]["status"] == "published"

    def test_the_month_the_calendar_runs_out_in_is_partial(self, seeded):
        # BB's calendar ending mid-month means that month's figure is real but
        # incomplete — the most misleading case if it were called "published".
        _, s = seeded                      # ends 2026-10-18, mid-October
        assert _month(monthly_by_product(s, 1, TODAY), "2026-10")["auction"]["status"] == "partial"

    def test_net_borrowing_is_flagged_when_the_two_sides_differ_in_span(self, seeded):
        # 20 years of inflows against a few weeks of auctions is not a forecast.
        _, s = seeded
        t = monthly_by_product(s, 20, TODAY)["totals"]
        assert t["net_borrowing_comparable"] is False
        assert t["auction_months_published"] < t["months"]


class TestFiscalYears:
    def test_june_and_july_fall_in_different_fiscal_years(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        assert _month(p, "2027-06")["fiscal_year"] == "2026-27"
        assert _month(p, "2027-07")["fiscal_year"] == "2027-28"

    def test_subtotals_tie_back_to_their_months(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        for fy in p["fy_subtotals"]:
            mine = [m for m in p["months"] if m["fiscal_year"] == fy["fiscal_year"]]
            assert fy["months"] == len(mine)
            for kind in ("redemption", "coupon", "auction"):
                assert round(sum(m[kind]["total"] for m in mine), 2) == fy[kind]["total"]

    def test_subtotals_add_up_to_the_grand_total(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        for kind in ("redemption", "coupon", "auction"):
            assert round(sum(f[kind]["total"] for f in p["fy_subtotals"]), 2) == \
                   p["totals"][kind]["total"]


class TestPerProductNetting:
    """The report netted only at month level, so it could not say where you are
    long or short a single product."""

    def test_each_products_net_is_its_own_inflow_minus_its_own_outflow(self, seeded):
        _, s = seeded
        oct26 = _month(monthly_by_product(s, 2, TODAY), "2026-10")
        bp = oct26["by_product"]
        # T-Bond: 500 principal + 60 coupon in, nothing auctioned
        assert bp["T_BOND"]["inflow"] == 560.0
        assert bp["T_BOND"]["outflow"] == 0.0
        assert bp["T_BOND"]["net"] == 560.0
        # T-Bill: 3,000 principal in (no coupon -- zero-coupon), 3,500 auctioned out
        assert bp["T_BILL"]["inflow"] == 3000.0
        assert bp["T_BILL"]["coupon"] == 0.0
        assert bp["T_BILL"]["outflow"] == 3500.0
        assert bp["T_BILL"]["net"] == -500.0

    def test_the_products_inflows_sum_to_the_months_inflow(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        for m in p["months"]:
            assert round(sum(m["by_product"][k]["inflow"] for k in p["products"]), 2) == \
                m["inflow_total"], m["month"]

    def test_an_unpublished_month_withholds_the_product_net(self, seeded):
        # Netting a real inflow against an ABSENT outflow would report every
        # unpublished month as a large product surplus. A test asserting a
        # number here would pass on exactly the wrong behaviour.
        _, s = seeded
        far = _month(monthly_by_product(s, 2, TODAY), "2027-07")
        assert far["auction"]["status"] == "not_published"
        assert far["by_product"]["T_BOND"]["net"] is None
        assert far["by_product"]["T_BOND"]["outflow"] is None
        # the inflow side is still known and still reported
        assert far["by_product"]["FRTB"]["inflow"] == 200.0

    def test_the_horizon_roll_up_carries_a_net_per_product(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        bp = p["totals"]["by_product"]
        assert bp["T_BILL"]["redemption"] == 3000.0
        assert bp["T_BILL"]["outflow"] == 3500.0
        # the window reaches past BB's calendar, so the net is not comparable
        assert bp["T_BILL"]["net_comparable"] is False
        assert bp["T_BILL"]["net"] is None

    def test_a_fully_published_window_does_give_a_product_net(self):
        eng, s = _mem()
        _sec(s, "BD0BILL00001", "T_BILL")
        _mat(s, "BD0BILL00001", D(2026, 10, 20), 30000.0)
        _auc(s, D(2026, 10, 18), "T_BILL", 35000.0)
        _auc(s, D(2026, 10, 31), "T_BILL", 1.0)        # calendar covers the month
        s.commit()
        bp = monthly_by_product(s, 1, TODAY)["months"][0]["by_product"]
        assert bp["T_BILL"]["net"] == round(3000.0 - 3500.1, 2)


class TestHorizon:
    def test_twenty_years_is_allowed_and_one_month_shy_of_it_is_the_end(self, seeded):
        _, s = seeded
        p = monthly_by_product(s, 20, TODAY)
        assert len(p["months"]) == 240
        assert p["from"] == "2026-10-01" and p["to"] == "2046-09-30"

    @pytest.mark.parametrize("years", [0, 21, -1])
    def test_a_horizon_outside_one_to_twenty_is_refused(self, seeded, years):
        _, s = seeded
        with pytest.raises(ValueError):
            monthly_by_product(s, years, TODAY)

    def test_the_last_dated_month_is_named_so_empty_tails_read_as_empty(self, seeded):
        # A 20-year window outruns the longest bond, so it ends in real zeros.
        # Saying where flows stop keeps that from looking like a data gap.
        _, s = seeded
        assert monthly_by_product(s, 20, TODAY)["last_month_with_flows"] == "2027-07"


class TestDetailTiesToTheMonthlyTable:
    def test_every_event_appears_once_and_sums_to_the_month(self, seeded):
        # The detail sheet is what makes a month auditable; if it did not tie to
        # the table beside it, it would be worse than not shipping it.
        _, s = seeded
        p = monthly_by_product(s, 2, TODAY)
        d = event_detail(s, 2, TODAY)
        assert d["count"] == 6            # 3 maturities + 2 coupons + 1 auction
        for m in p["months"]:
            for kind, key in (("REDEMPTION", "redemption"), ("COUPON", "coupon"),
                              ("AUCTION", "auction")):
                rows = [r for r in d["rows"] if r["month"] == m["month"] and r["kind"] == kind]
                assert round(sum(r["amount_crore"] for r in rows), 2) == m[key]["total"], \
                    (m["month"], kind)

    def test_auctions_are_itemised_at_all(self, seeded):
        # They were not: event_detail queried only maturity_events and
        # coupon_events, so the OUTFLOW half of the report could not be traced
        # to source or exported per auction.
        _, s = seeded
        rows = [r for r in event_detail(s, 2, TODAY)["rows"] if r["kind"] == "AUCTION"]
        assert len(rows) == 1
        r = rows[0]
        assert r["direction"] == "OUTFLOW"
        assert r["product"] == "T_BILL" and r["tenor_label"] == "91D"
        assert r["amount_crore"] == 3500.0
        assert r["isin"] is None          # an auction is an event, not an instrument

    def test_a_planned_auction_is_marked_so_it_is_not_summed_as_settled(self):
        # BB's calendar target is a plan. Summing it as an actual would report
        # borrowing that has not happened.
        eng, s = _mem()
        _sec(s, "BD0BILL00001", "T_BILL")
        s.add(AuctionEvent(fiscal_year="2026-27", auction_date=D(2026, 11, 1),
                           settlement_date=D(2026, 11, 2), security_type="T_BILL",
                           tenor_label="91D", offered_amount_bdt_mill=20000.0,
                           outflow_status="PLANNED"))
        s.commit()
        rows = [r for r in event_detail(s, 1, TODAY)["rows"] if r["kind"] == "AUCTION"]
        assert rows[0]["status"] == "PLANNED"
        # with nothing accepted yet the figure falls back to the offered amount
        assert rows[0]["amount_crore"] == 2000.0
        assert rows[0]["offered_crore"] == 2000.0

    def test_detail_carries_the_product_and_both_units(self, seeded):
        _, s = seeded
        row = next(r for r in event_detail(s, 2, TODAY)["rows"] if r["isin"] == "BD0BILL00001")
        assert row["product"] == "T_BILL"
        assert row["amount_crore"] == 3000.0 and row["amount_mill"] == 30000.0
        assert row["kind"] == "REDEMPTION"


class TestTheDeploymentBoundary:
    """These two tests exist because the first version of this feature returned
    500 in production while passing every local test. The logic lived in
    engines/, which the API cannot see: it is deployed with api/ as its root and
    api/index.py puts only that directory on sys.path. Locally the repo root is
    always importable, so nothing failed until it was live."""

    def _source(self):
        import schedule_logic
        return io.open(schedule_logic.__file__, encoding="utf-8").read()

    def test_the_module_imports_nothing_from_the_repo_root(self):
        # config, db, engines and calendar_utils all live above api/ and are not
        # uploaded with it. Importing any of them is a production-only failure.
        import ast
        forbidden = {"config", "db", "engines", "calendar_utils", "validate", "fetchers",
                     "parsers"}
        bad = []
        for node in ast.walk(ast.parse(self._source())):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in forbidden:
                    bad.append(node.module)
        assert not bad, f"api/routers/schedule_logic.py cannot import from the repo root: {bad}"

    def test_the_duplicated_fiscal_year_agrees_with_the_real_one(self):
        # The duplication is deliberate (see the module comment) but it must not
        # drift: the FY boundary decides which subtotal every month lands in.
        import schedule_logic
        from config import fiscal_year as canonical
        d = D(2024, 1, 1)
        while d < D(2030, 1, 1):
            assert schedule_logic.fiscal_year(d) == canonical(d), d
            d += datetime.timedelta(days=1)

    def test_the_crore_conversion_agrees_too(self):
        import schedule_logic
        from config import CRORE_TO_MILLION as canonical
        assert schedule_logic.CRORE_TO_MILLION == canonical
