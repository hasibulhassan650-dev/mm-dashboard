"""The desk's own cut-off, and the two rules that make it safe to have.

BB's results page prints the ISSUE date and the auction was the previous
working day, so a published cut-off arrives a day or two after the desk has
already traded off it. The desk is in the auction; this is where it records
what it saw, while that is still worth anything.

Rule one: **a desk mark is never BB data.** It goes to its own table, it is
labelled provisional wherever it appears, and BB's published figure is
preferred the instant it exists. One unverified number sitting inside
primary_yield_snapshots, indistinguishable from the rest, would undo the thing
this whole project is for.

Rule two: **the write path fails closed.** This API is public, so a shared
secret is the only thing between it and a forged cut-off. No secret configured
means no writes — not a default, not a hash baked into a public repo, not
"allow while unconfigured". These tests exist because that is exactly the kind
of gate that gets loosened by accident later.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
# APPENDED, and api/routers rather than api/: putting api/ first would shadow
# the repo-root db module with the API's own cut-down one. Same pattern as
# tests/test_exports.py — the api/ deploy boundary is real, and the tests have
# to respect it without breaking the root imports.
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DeskMark, PrimaryYieldSnapshot

D = datetime.date
TODAY = D.today()


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


@pytest.fixture
def desk(monkeypatch):
    """The API router, pointed at an in-memory database."""
    import desk as mod
    eng, s = _mem()
    monkeypatch.setattr(mod, "get_session", lambda: sessionmaker(bind=eng)())
    return mod, eng, s


def _mark(**kw):
    import desk as mod
    base = {"auction_date": TODAY, "tenor_label": "91D", "cutoff_yield_pct": 8.5899}
    return mod.MarkIn(**{**base, **kw})


class TestTheWritePathFailsClosed:
    def test_no_secret_configured_means_no_writes(self, desk, monkeypatch):
        mod, _, _ = desk
        monkeypatch.delenv("DESK_WRITE_SECRET", raising=False)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.put_mark(_mark(), x_desk_secret="anything")
        assert exc.value.status_code == 503
        assert "unset" in exc.value.detail

    def test_an_empty_secret_is_not_a_secret(self, desk, monkeypatch):
        """A blank env var must not count as configured — that is how a
        'temporarily unset' variable turns into an open write endpoint."""
        mod, _, _ = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.put_mark(_mark(), x_desk_secret="")
        assert exc.value.status_code == 503

    def test_a_wrong_secret_is_refused(self, desk, monkeypatch):
        mod, _, _ = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "right")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.put_mark(_mark(), x_desk_secret="wrong")
        assert exc.value.status_code == 403

    def test_a_missing_header_is_refused(self, desk, monkeypatch):
        mod, _, _ = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "right")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.put_mark(_mark(), x_desk_secret=None)
        assert exc.value.status_code == 403

    def test_nothing_is_stored_by_a_refused_write(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.delenv("DESK_WRITE_SECRET", raising=False)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            mod.put_mark(_mark(), x_desk_secret="anything")
        assert s.query(DeskMark).count() == 0


class TestWhatItAccepts:
    def test_a_good_mark_is_recorded(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        out = mod.put_mark(_mark(entered_by="HH"), x_desk_secret="k")
        assert out["ok"] and out["action"] == "recorded"
        row = s.query(DeskMark).one()
        assert (row.tenor_label, row.cutoff_yield_pct, row.entered_by) == ("91D", 8.5899, "HH")

    def test_the_same_auction_and_tenor_corrects_rather_than_duplicates(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        mod.put_mark(_mark(cutoff_yield_pct=8.50), x_desk_secret="k")
        out = mod.put_mark(_mark(cutoff_yield_pct=8.5899), x_desk_secret="k")
        assert out["action"] == "updated"
        assert s.query(DeskMark).count() == 1
        assert s.query(DeskMark).one().cutoff_yield_pct == 8.5899

    def test_an_unknown_tenor_is_rejected(self):
        """Free text would let a typo invent a tenor that then shows up on the
        curve as a real point."""
        with pytest.raises(Exception) as exc:
            _mark(tenor_label="9ID")
        assert "unknown tenor" in str(exc.value)

    def test_a_future_auction_has_no_cutoff_to_know(self):
        with pytest.raises(Exception) as exc:
            _mark(auction_date=TODAY + datetime.timedelta(days=1))
        assert "future" in str(exc.value)

    def test_an_impossible_yield_is_rejected(self):
        for bad in (0, -1, 31, 105.4081):   # 105.4081 is a PRICE, not a yield
            with pytest.raises(Exception):
                _mark(cutoff_yield_pct=bad)

    def test_an_old_auction_is_refused_because_bb_has_published(self):
        with pytest.raises(Exception) as exc:
            _mark(auction_date=TODAY - datetime.timedelta(days=45))
        assert "published by now" in str(exc.value)


class TestItNeverBecomesBBData:
    def test_the_published_table_is_untouched(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        mod.put_mark(_mark(), x_desk_secret="k")
        assert s.query(PrimaryYieldSnapshot).count() == 0, \
            "a desk mark reached BB's own table of record"

    def test_bbs_figure_is_the_one_shown_once_published(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        mod.put_mark(_mark(cutoff_yield_pct=8.60), x_desk_secret="k")
        s.add(PrimaryYieldSnapshot(
            snapshot_date=TODAY, auction_date=TODAY, issue_date=TODAY,
            security_type="T_BILL", tenor_label="91D", tenor_years=0.25,
            cutoff_yield_pct=8.5899, source="BB Treasury"))
        s.commit()
        got = mod.get_marks(days=45)
        m = got["marks"][0]
        assert m["confirmed"] is True
        assert m["shown_cutoff_pct"] == 8.5899
        assert m["shown_source"] == "BB"
        # The mark itself is kept: it is the record of what the desk believed.
        assert m["cutoff_yield_pct"] == 8.60
        assert m["diff_bps"] == 1.0

    def test_until_bb_publishes_the_mark_is_labelled_provisional(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        mod.put_mark(_mark(), x_desk_secret="k")
        m = mod.get_marks(days=45)["marks"][0]
        assert m["confirmed"] is False
        assert "provisional" in m["shown_source"].lower()
        assert m["diff_bps"] is None

    def test_the_read_path_says_whether_writing_is_even_configured(self, desk, monkeypatch):
        """So the form can refuse to accept a figure it would have to drop."""
        mod, _, _ = desk
        monkeypatch.delenv("DESK_WRITE_SECRET", raising=False)
        assert mod.get_marks(days=45)["configured"] is False
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        assert mod.get_marks(days=45)["configured"] is True


class TestTheGuardianReadsTheDifference:
    def _issues(self, monkeypatch, desk_cut, bb_cut, days_ago=0):
        import validate
        eng, s = _mem()
        ad = TODAY - datetime.timedelta(days=days_ago)
        s.add(DeskMark(auction_date=ad, tenor_label="91D", cutoff_yield_pct=desk_cut,
                       entered_by="HH", entered_utc=datetime.datetime.utcnow()))
        if bb_cut is not None:
            s.add(PrimaryYieldSnapshot(
                snapshot_date=ad, auction_date=ad, issue_date=ad, security_type="T_BILL",
                tenor_label="91D", tenor_years=0.25, cutoff_yield_pct=bb_cut,
                source="BB Treasury"))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check()
        return [i for i in rep["issues"] + rep["waiting"] + rep["notices"]
                if i.startswith("desk")]

    def test_a_close_read_is_silent(self, monkeypatch):
        assert self._issues(monkeypatch, 8.59, 8.5899) == []

    def test_a_wide_miss_is_reported_but_not_as_a_data_defect(self, monkeypatch):
        """BB's figure is already the one in use, so the data is fine. The desk
        still wants to know its read was 41bp out."""
        found = self._issues(monkeypatch, 9.00, 8.5899)
        assert found and "41" in found[0]
        assert "not a data defect" in found[0]

    def test_a_mark_bb_has_not_confirmed_in_a_week_is_surfaced(self, monkeypatch):
        found = self._issues(monkeypatch, 8.59, None, days_ago=12)
        assert found, "the site is showing the desk's own figure and saying nothing"
        assert "unconfirmed" in found[0]

    def test_a_fresh_unconfirmed_mark_is_not_nagged_about(self, monkeypatch):
        assert self._issues(monkeypatch, 8.59, None, days_ago=0) == []


class TestThePingTellsTheDeskWhichSideIsMissing:
    """The form used to simply refuse, and the desk could not tell whether the
    site, the API, or both-with-different-values was the problem. The ping
    checks the shared secret WITHOUT writing, with the same fail-closed gate."""

    def test_unset_means_503_never_ok(self, desk, monkeypatch):
        mod, _, s = desk
        monkeypatch.delenv("DESK_WRITE_SECRET", raising=False)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.ping(x_desk_secret="anything")
        assert exc.value.status_code == 503

    def test_a_wrong_secret_is_403(self, desk, monkeypatch):
        mod, _, _ = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "right")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.ping(x_desk_secret="wrong")
        assert exc.value.status_code == 403

    def test_the_right_secret_is_ok_and_reveals_nothing(self, desk, monkeypatch):
        mod, _, _ = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "s3cret-value")
        out = mod.ping(x_desk_secret="s3cret-value")
        assert out == {"ok": True, "configured": True}
        assert "s3cret" not in str(out)

    def test_it_never_writes(self, desk, monkeypatch):
        mod, eng, s = desk
        monkeypatch.setenv("DESK_WRITE_SECRET", "k")
        mod.ping(x_desk_secret="k")
        assert s.query(DeskMark).count() == 0


class TestTheCurveShowsTheDeskMarkUntilBBPublishes:
    """The desk reads a cut-off the day it happens; BB prints it later. The curve was a day behind
    what the desk knew, so the mark is drawn on it - as its own provisional layer, never as BB's."""

    @pytest.fixture
    def curve(self, monkeypatch):
        import yields as ymod
        eng, s = _mem()
        monkeypatch.setattr(ymod, "get_session", lambda: sessionmaker(bind=eng)())
        return ymod, s

    def _bb(self, s, when, tenor="91D", yld=8.50, years=0.25):
        s.add(PrimaryYieldSnapshot(snapshot_date=when, auction_date=when, issue_date=when,
                                   security_type="T_BILL", tenor_label=tenor, tenor_years=years,
                                   cutoff_yield_pct=yld, source="BB Treasury"))

    def _mark(self, s, when, tenor="91D", yld=8.60):
        s.add(DeskMark(auction_date=when, tenor_label=tenor, security_type="T_BILL", cutoff_yield_pct=yld,
                       entered_by="desk"))

    def test_a_mark_newer_than_bbs_latest_is_on_the_curve_and_labelled(self, curve):
        ymod, s = curve
        self._bb(s, TODAY - datetime.timedelta(days=7)); self._mark(s, TODAY); s.commit()
        got = ymod.get_provisional_curve()
        assert len(got) == 1 and got[0]["tenor_label"] == "91D" and got[0]["cutoff_yield_pct"] == 8.60
        assert got[0]["source"] == "DESK (provisional)"
        assert got[0]["diff_bps_vs_bb_latest"] == 10.0 and got[0]["tenor_years"] == 0.25

    def test_it_drops_off_the_moment_bb_publishes_that_auction(self, curve):
        """Otherwise the desk's read would sit beside BB's own figure for the same auction."""
        ymod, s = curve
        self._bb(s, TODAY); self._mark(s, TODAY); s.commit()
        assert ymod.get_provisional_curve() == []

    def test_an_old_mark_behind_a_later_bb_auction_is_not_shown(self, curve):
        ymod, s = curve
        self._mark(s, TODAY - datetime.timedelta(days=14)); self._bb(s, TODAY - datetime.timedelta(days=7)); s.commit()
        assert ymod.get_provisional_curve() == []

    def test_only_the_latest_mark_per_tenor_and_bbs_own_curve_is_unchanged(self, curve):
        ymod, s = curve
        self._bb(s, TODAY - datetime.timedelta(days=14))
        self._mark(s, TODAY - datetime.timedelta(days=7), yld=8.55); self._mark(s, TODAY, yld=8.62); s.commit()
        got = ymod.get_provisional_curve()
        assert [g["cutoff_yield_pct"] for g in got] == [8.62]
        assert s.query(PrimaryYieldSnapshot).count() == 1, "a desk mark reached BB's own table"
