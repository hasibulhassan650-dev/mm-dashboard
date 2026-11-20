"""A recorded BB hole may explain the shortfall it causes - and nothing else.

17-Oct-2026 is a recorded OMO absence (BB never posted that day's release). validate.py excused ANY maturity shortfall
within 6 days of "17-Oct + a standard tenor", with no amount check and no working-day roll. So two real findings went
quiet for weeks: AR 7D 36.73 on 27-Oct (excused by 10 - 7 = 3) and the lost 4-May AR 180D deal inside 1-Nov (excused by
the 14-day window next to it), while deep_audit never consulted the absences at all and failed six weeks running on
gaps the gate had already explained. These tests pin the exact rule: deal day + the tenor of BB's maturity row, rolled
to a working day, bounded to that row's own gap.
"""
import datetime
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import calendar_utils
import db as dbmod
from db import DataAbsence, OMOTransaction
from engines import absences as A

D = datetime.date
ABSENT = {D(2026, 10, 17)}                       # Saturday, a declared working day, BB never posted it
HOL = {D(2026, 10, 20), D(2026, 10, 21), D(2026, 10, 22)}
EXC_180 = {"dataset": "omo", "instrument": "AR", "date": D(2026, 11, 1), "tenor_days": 180, "gap_crore": 734.71,
           "tolerance_crore": 0.10, "status": "lost_deal", "reason": "700.00 x (1 + 0.10 x 181/365) = 734.71"}
EXC_UNEXPLAINED = {"dataset": "omo", "instrument": "AR", "date": D(2026, 10, 27), "tenor_days": 7, "gap_crore": 36.73,
                   "tolerance_crore": 0.10, "status": "unexplained", "reason": "BB's own print"}


@pytest.fixture(autouse=True)
def _calendar(monkeypatch):
    monkeypatch.setattr(calendar_utils, "_holiday_set", set(HOL))
    monkeypatch.setattr(calendar_utils, "_working_weekend_set", {D(2026, 10, 17)})


def judge(inst, day, printed, ours, absent=ABSENT, exceptions=()):
    return A.judge_ledger_day(inst, day, printed, ours, absent, exceptions, HOL)


class TestTheWorkingDayRoll:
    def test_a_deal_on_the_missing_day_matures_where_the_ledger_looks(self):
        """17-Oct (Sat) +1 = Sun 18-Oct; +7 = Sat 24-Oct -> Sun 25-Oct; +14 = Sat 31-Oct -> Sun 1-Nov."""
        assert A.absence_maturity_day(D(2026, 10, 17), 1, HOL) == D(2026, 10, 18)
        assert A.absence_maturity_day(D(2026, 10, 17), 7, HOL) == D(2026, 10, 25)
        assert A.absence_maturity_day(D(2026, 10, 17), 14, HOL) == D(2026, 11, 1)

    def test_without_the_roll_the_1_nov_shortfall_would_not_be_explained(self):
        """The 14-day deal's raw date is 31-Oct (a Saturday, nothing matures). If the roll is lost the 14D part of
        1-Nov stops being excused and the audit goes red on a gap the 17-Oct absence does explain."""
        ok, notes, excused = judge("AR", D(2026, 11, 1), {1: 779.19, 14: 445.35}, {1: 779.0, 14: 51.88})
        assert ok and excused == pytest.approx(393.47, abs=0.01) and notes


class TestWhatAnAbsenceExplains:
    def test_18_oct_one_day_maturities_are_BBs_omission(self):
        ok, notes, excused = judge("AR", D(2026, 10, 18), {1: 3471.54}, {})
        assert ok and excused == pytest.approx(3471.54) and notes[0][0] == "known"
        ok, _, _ = judge("SDF", D(2026, 10, 18), {1: 1634.34}, {})
        assert ok

    def test_the_27_oct_seven_day_shortfall_is_NOT_excused_by_the_17_oct_absence(self):
        """17-Oct + 7 rolls to 25-Oct. The old window (10 - 7 = 3 <= 6) hid this 36.73 cr BB print for weeks."""
        ok, notes, _ = judge("AR", D(2026, 10, 27), {1: 1000.26, 7: 36.73}, {1: 1000.26})
        assert not ok and not notes

    def test_a_recorded_absence_is_not_a_switch_that_turns_the_check_off(self):
        """Same shortfall on a day no absence + tenor reaches: still a defect."""
        ok, _, _ = judge("AR", D(2026, 10, 19), {1: 3471.54}, {})
        assert not ok

    def test_the_180_day_part_of_1_nov_is_not_excused_by_the_14_day_window_beside_it(self):
        """The old rule excused the whole of 1-Nov because 15 - 14 = 1 <= 6; the 180-day gap is a different fact."""
        ok, _, excused = judge("AR", D(2026, 11, 1), {1: 779.19, 14: 445.35, 180: 2903.34},
                               {1: 779.0, 14: 51.88, 180: 2168.63})
        assert not ok and excused == pytest.approx(393.47, abs=0.01)


class TestAnExcusalNeverExceedsTheGap:
    @pytest.mark.parametrize("ours", [{}, {1: 1000.0}, {1: 3000.0}])
    def test_excused_is_at_most_the_shortfall(self, ours):
        printed = {1: 3471.54}
        ok, _, excused = judge("AR", D(2026, 10, 18), printed, ours)
        assert excused <= max(0.0, printed[1] - ours.get(1, 0.0)) + 1e-9

    def test_a_surplus_elsewhere_is_not_hidden_behind_an_excused_shortfall(self):
        """An excused 1-day shortfall must not also swallow a 300 cr phantom 7-day tranche on the same day."""
        ok, _, excused = judge("AR", D(2026, 10, 18), {1: 3471.54, 7: 100.0}, {7: 400.0})
        assert not ok and excused == pytest.approx(3471.54)

    def test_a_tenor_mislabelled_print_does_not_let_an_absence_excuse_a_real_hole(self):
        """BB printed our corrected 2-day tranche under 7D (our row sits under tenor 2). That tranche covers the 7D
        print; the 1D shortfall beside it is the absence's. But the unmatched tranche cannot be spent twice: if it
        covers only part of the 7D print, the rest is a real hole and the day fails."""
        ok, _, excused = judge("CB_REPO", D(2026, 10, 18), {1: 500.0, 7: 15870.0}, {2: 15870.0})
        assert ok and excused == pytest.approx(500.0)
        ok, _, _ = judge("CB_REPO", D(2026, 10, 18), {1: 500.0, 7: 15870.0}, {2: 15000.0})
        assert not ok


class TestTheKnownLedgerExceptions:
    def test_the_180_day_lost_deal_is_excused_and_reported(self):
        ok, notes, excused = judge("AR", D(2026, 11, 1), {1: 779.19, 14: 445.35, 180: 2903.34},
                                   {1: 779.0, 14: 51.88, 180: 2168.63}, exceptions=[EXC_180])
        assert ok and excused == pytest.approx(734.71 + 393.47, abs=0.02)
        assert any(sev == "known" and "KNOWN (lost deal" in m and "734.71" in m for sev, m in notes), notes

    def test_the_180_day_exception_cannot_excuse_a_different_amount(self):
        """If the 180D gap is 1,234.71 (another 500 cr missing) the 734.71 proof no longer fits and the day is a defect."""
        ok, _, _ = judge("AR", D(2026, 11, 1), {1: 779.19, 14: 445.35, 180: 3403.34},
                         {1: 779.0, 14: 51.88, 180: 2168.63}, exceptions=[EXC_180])
        assert not ok

    def test_the_exception_does_not_travel_to_another_day_or_tenor(self):
        ok, _, _ = judge("AR", D(2026, 11, 2), {180: 2903.34}, {180: 2168.63}, exceptions=[EXC_180])
        assert not ok
        ok, _, _ = judge("AR", D(2026, 11, 1), {91: 734.71}, {}, exceptions=[EXC_180])
        assert not ok

    def test_the_unexplained_item_is_reported_loudly_as_unexplained(self):
        ok, notes, _ = judge("AR", D(2026, 10, 27), {1: 1000.26, 7: 36.73}, {1: 1000.26}, exceptions=[EXC_UNEXPLAINED])
        assert ok and notes and notes[0][0] == "unexplained" and "UNEXPLAINED" in notes[0][1]

    def test_the_shipped_entries_carry_their_evidence(self):
        got = {(e["date"], e["tenor_days"]): e for e in A.known_ledger_exceptions()}
        lost, unexplained = got[(D(2026, 11, 1), 180)], got[(D(2026, 10, 27), 7)]
        assert lost["status"] == "lost_deal" and "700.00" in lost["reason"] and "2,903.34" in lost["reason"]
        assert unexplained["status"] == "unexplained" and "no deal in our record explains" in unexplained["reason"]
        # the arithmetic in the proof must actually hold, or the entry is an assertion
        assert 700.0 * (1 + 0.10 * 181 / 365) == pytest.approx(lost["gap_crore"], abs=0.005)
        assert 2065.63 * (1 + 0.10 * 182 / 365) + lost["gap_crore"] == pytest.approx(2903.34, abs=0.005)


class TestGrowthByTheDaysThatActuallyRan:
    def test_a_maturity_rolled_past_a_weekend_pays_the_extra_days(self):
        """3-May AR '180D' matured Sunday 1-Nov = 182 days; BB's print reconciles to the paisa only that way."""
        got = A.grown_principal(2065.63, 10.0, 180, D(2026, 5, 3), D(2026, 11, 1))
        assert round(got, 2) == 2168.63
        assert round(2065.63 * (1 + 0.10 * 180 / 365), 2) == 2167.50          # the label would be 1.13 cr short

    def test_the_full_history_audit_uses_the_real_days_so_the_exception_still_fits(self):
        """Growing by the 180-day label makes the gap 735.84, outside the exception's 0.10 tolerance: the audit would
        call the proven 4-May loss a fresh defect again."""
        import deep_audit
        eng = create_engine("sqlite:///:memory:")
        dbmod.Base.metadata.create_all(eng)
        s = sessionmaker(bind=eng)()
        s.add(OMOTransaction(transaction_date=D(2025, 1, 5), maturity_date=D(2025, 1, 6), instrument="SDF", tenor_label="1D",
                             tenor_days=1, accepted_bdt_crore=1.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))
        s.add(OMOTransaction(transaction_date=D(2026, 5, 3), maturity_date=D(2026, 11, 1), instrument="AR", tenor_label="180D",
                             tenor_days=180, accepted_bdt_crore=2065.63, maturity_bdt_crore=0.0, rate_pct=10.0, direction="INJECTION"))
        s.add(OMOTransaction(transaction_date=D(2026, 11, 1), maturity_date=D(2027, 5, 2), instrument="AR", tenor_label="180D",
                             tenor_days=180, accepted_bdt_crore=0.0, maturity_bdt_crore=2903.34, rate_pct=10.0, direction="INJECTION"))
        s.commit()
        bad, known, unexplained = deep_audit.omo_ledger_full(s, absent_dates=set(), exceptions=[EXC_180])
        assert bad == [] and any("KNOWN (lost deal" in k for k in known) and unexplained == []
        bad, known, _ = deep_audit.omo_ledger_full(s, absent_dates=set(), exceptions=[])
        assert len(bad) == 1 and "missing/misdated injection" in bad[0]


class TestTheGateAgrees:
    """validate.py's omo-ledger rule on an in-memory DB: the same rule, so the gate and the audit cannot disagree."""

    @staticmethod
    def _day():
        d = D.today() - datetime.timedelta(days=5)
        while d.weekday() not in (6, 0, 1, 2, 3):
            d -= datetime.timedelta(days=1)
        return d

    def _issues(self, monkeypatch, absent_offset):
        import validate
        day = self._day()
        eng = create_engine("sqlite:///:memory:")
        dbmod.Base.metadata.create_all(eng)
        s = sessionmaker(bind=eng)()
        old = D.today() - datetime.timedelta(days=400)
        s.add(OMOTransaction(transaction_date=old, maturity_date=old, instrument="SDF", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=1.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))
        s.add(OMOTransaction(transaction_date=day, maturity_date=day, instrument="AR", tenor_label="7D", tenor_days=7,
                             accepted_bdt_crore=0.0, maturity_bdt_crore=36.73, rate_pct=9.5, direction="INJECTION"))
        s.add(DataAbsence(dataset="omo", absent_date=day - datetime.timedelta(days=absent_offset), kind="bb_omission",
                          reason="BB never posted it", verified_utc=datetime.datetime.utcnow(), checks=1))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check(limit_per_rule=200)
        return [i for i in rep["issues"] if i.startswith("omo-ledger")], \
               [n for n in rep["notices"] if n.startswith("omo-ledger")]

    def test_a_seven_day_shortfall_is_excused_only_by_a_deal_exactly_seven_days_back(self, monkeypatch):
        issues, notices = self._issues(monkeypatch, 7)
        assert not issues and any("7-day deal" in n for n in notices), (issues, notices)

    def test_ten_days_back_is_the_old_loose_window_and_must_fail(self, monkeypatch):
        issues, _ = self._issues(monkeypatch, 10)
        assert any("AR" in i and "missing/misdated injection" in i for i in issues), issues
