"""The desk's own figure for a buyback BB has announced but not yet resulted.

BB's first buyback settled on 12-Oct-2026; its figures reached the system days
later, though the desk knew the accepted face value on the day. Like a cut-off
mark this is provisional and never BB data, and one rule matters above the rest:
it must NEVER be added to a ladder total. A cash figure the desk then trades on
has to come from BB's own event, not from something typed in.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import BuybackEvent, DeskBuybackMark, Security

D = datetime.date
TODAY = D.today()
ISIN = "BD0926191026"


@pytest.fixture
def desk(monkeypatch):
    import desk as mod
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(Security(isin=ISIN, security_name_norm="2Y BGTB", security_type="T_BOND"))
    s.commit()
    monkeypatch.setattr(mod, "get_session", lambda: sessionmaker(bind=eng)())
    monkeypatch.setenv("DESK_WRITE_SECRET", "k")
    return mod, eng, s


def _b(**kw):
    import desk as mod
    base = {"isin": ISIN, "settlement_date": TODAY, "accepted_face_crore": 1716.14}
    return mod.BuybackIn(**{**base, **kw})


class TestFailsClosed:
    def test_no_secret_no_write(self, desk, monkeypatch):
        mod, _, s = desk
        monkeypatch.delenv("DESK_WRITE_SECRET", raising=False)
        with pytest.raises(HTTPException) as e:
            mod.put_buyback(_b(), x_desk_secret="k")
        assert e.value.status_code == 503 and s.query(DeskBuybackMark).count() == 0

    def test_a_wrong_secret_is_refused(self, desk):
        mod, _, s = desk
        with pytest.raises(HTTPException) as e:
            mod.put_buyback(_b(), x_desk_secret="nope")
        assert e.value.status_code == 403 and s.query(DeskBuybackMark).count() == 0


class TestWhatItAccepts:
    def test_a_good_figure_is_recorded_and_a_second_corrects_it(self, desk):
        mod, _, s = desk
        assert mod.put_buyback(_b(), x_desk_secret="k")["action"] == "recorded"
        assert mod.put_buyback(_b(accepted_face_crore=1700.0), x_desk_secret="k")["action"] == "updated"
        rows = s.query(DeskBuybackMark).all()
        assert len(rows) == 1 and rows[0].accepted_face_crore == 1700.0

    def test_cash_is_stored_only_when_given_never_derived(self, desk):
        """BB did not publish the price of the first buyback. Deriving cash from
        face x 100 would put a premium-priced bond's cash wrong by the premium."""
        mod, _, s = desk
        mod.put_buyback(_b(), x_desk_secret="k")
        assert s.query(DeskBuybackMark).one().cash_paid_crore is None

    def test_an_isin_we_do_not_hold_is_refused(self, desk):
        """A typo here would otherwise look like a real bond."""
        mod, _, s = desk
        with pytest.raises(HTTPException) as e:
            mod.put_buyback(_b(isin="BD0000000000"), x_desk_secret="k")
        assert e.value.status_code == 422 and s.query(DeskBuybackMark).count() == 0

    def test_nonsense_is_rejected_by_the_model(self):
        import desk as mod
        for bad in ({"isin": "abc"}, {"accepted_face_crore": 0}, {"accepted_face_crore": -5},
                    {"price_per_100": 0}, {"price_per_100": 500},
                    {"settlement_date": TODAY + datetime.timedelta(days=90)},
                    {"settlement_date": TODAY - datetime.timedelta(days=90)}):
            with pytest.raises(ValueError):
                _b(**bad)

    def test_the_isin_is_normalised(self):
        assert _b(isin=" bd0926191026 ").isin == ISIN


class TestProvisionalAndNeverInATotal:
    def test_it_is_listed_unconfirmed_until_BB_has_the_event(self, desk):
        mod, eng, s = desk
        mod.put_buyback(_b(), x_desk_secret="k")
        out = mod.desk_buybacks(s, TODAY - datetime.timedelta(days=45))
        assert out[0]["confirmed"] is False and out[0]["shown_source"] == "DESK (provisional)"

    def test_once_BB_publishes_BBs_figure_is_the_one_shown_and_the_gap_is_kept(self, desk):
        mod, eng, s = desk
        mod.put_buyback(_b(accepted_face_crore=1700.0), x_desk_secret="k")
        s.add(BuybackEvent(isin=ISIN, settlement_date=TODAY, accepted_face_crore=1716.14))
        s.commit()
        out = mod.desk_buybacks(s, TODAY - datetime.timedelta(days=45))[0]
        assert out["confirmed"] and out["shown_source"] == "BB"
        assert out["diff_face_crore"] == -16.14
        assert s.query(DeskBuybackMark).count() == 1       # kept, not overwritten

    def test_the_desk_figure_never_reaches_the_cash_ladder(self):
        """Grep, not trust: if a ladder source ever mentions the desk table, a typed
        number is moving a cumulative the desk then funds against."""
        for f in ("liquidity_logic.py", "model_logic.py"):
            src = open(os.path.join(_ROOT, "api", "routers", f), encoding="utf-8").read()
            assert "desk_buyback" not in src, f
        agg = open(os.path.join(_ROOT, "engines", "aggregation.py"), encoding="utf-8").read()
        assert "desk_buyback" not in agg and "DeskBuyback" not in agg

    def test_the_day_page_lists_it_apart_from_the_totals(self):
        src = open(os.path.join(_ROOT, "api", "routers", "flows.py"), encoding="utf-8").read()
        assert '"desk_buybacks": desk_bb' in src
        assert "NOT EXISTS (SELECT 1 FROM buyback_events" in src
