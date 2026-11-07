"""A hole BB left must stop failing the pipeline - but only the hole it explains.

17-Oct-2026 was a working Saturday on which BB operated and never posted the OMO release (serial 471 is
missing from its press list). Every scheduled run then went red on "no OMO operation that day" and on the
18-Oct maturity ledger (3,472 cr of Assured Repo maturing that no deal of ours explains), for something no
fetch can fix. A check that is always red is a check nobody reads. So the hole is recorded with its evidence;
the checks that it explains go quiet, and a hole that is NOT recorded still fails them.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import calendar_utils
import db as dbmod
from db import CallMoneyRate, DataAbsence, OMOTransaction
from engines import absences

D = datetime.date
TODAY = D.today()


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _yaml(tmp_path, body):
    f = tmp_path / "k.yaml"
    f.write_text(body, encoding="utf-8")
    return str(f)


GOOD = """absences:
  - dataset: omo
    date: 2026-10-17
    kind: bb_omission
    reason: BB posted no release; serial 471 missing
  - dataset: fxrates
    date: 2026-10-25
    kind: lost
    reason: not read while the runners were blocked
"""


class TestRecordingAKnownHole:
    def test_both_kinds_are_recorded_with_their_reason(self, tmp_path):
        eng, s = _mem()
        out = absences.sync_known_absences(s, _yaml(tmp_path, GOOD)); s.commit()
        assert len(out["recorded"]) == 2 and not out["errors"]
        rows = {(r.dataset, r.kind): r.reason for r in s.query(DataAbsence)}
        assert "serial 471" in rows[("omo", "bb_omission")] and ("fxrates", "lost") in rows

    def test_a_second_run_reconfirms_it_and_does_not_duplicate(self, tmp_path):
        eng, s = _mem()
        f = _yaml(tmp_path, GOOD)
        absences.sync_known_absences(s, f); s.commit()
        again = absences.sync_known_absences(s, f); s.commit()
        assert again["recorded"] == [] and s.query(DataAbsence).count() == 2
        assert s.query(DataAbsence).filter_by(dataset="omo").one().checks == 2

    def test_a_day_we_hold_data_for_is_never_called_absent(self, tmp_path):
        """'BB has nothing for that day' is false if we have a row for it."""
        eng, s = _mem()
        s.add(OMOTransaction(transaction_date=D(2026, 10, 17), maturity_date=D(2026, 10, 18), instrument="AR",
                             tenor_label="1D", tenor_days=1, accepted_bdt_crore=10.0, maturity_bdt_crore=0.0,
                             rate_pct=9.5, direction="INJECTION"))
        s.commit()
        out = absences.sync_known_absences(s, _yaml(tmp_path, GOOD))
        assert any("we hold omo data" in e for e in out["errors"]) and s.query(DataAbsence).filter_by(dataset="omo").count() == 0

    def test_a_reason_is_required_so_it_cannot_become_a_mute_button(self, tmp_path):
        eng, s = _mem()
        out = absences.sync_known_absences(s, _yaml(tmp_path, "absences:\n  - dataset: omo\n    date: 2026-10-17\n"))
        assert out["errors"] and not out["recorded"]

    def test_an_unknown_dataset_is_an_error(self, tmp_path):
        eng, s = _mem()
        out = absences.sync_known_absences(s, _yaml(tmp_path, "absences:\n  - dataset: nope\n    date: 2026-10-17\n    reason: x\n"))
        assert out["errors"]


def _working_days(n, ending_before):
    out, d = [], ending_before
    while len(out) < n:
        d -= datetime.timedelta(days=1)
        if d.weekday() in (6, 0, 1, 2, 3):
            out.append(d)
    return sorted(out)


class TestWhatTheLedgerExplains:
    """OMO ran every working day but one; call money traded every day including the missing one."""

    def _world(self):
        days = _working_days(8, TODAY)
        missing = days[3]
        nxt = days[4]
        eng, s = _mem()
        for d in days:
            s.add(CallMoneyRate(trade_date=d, product="Overnight", maturity_days=1, amount_crore=100.0, average_rate_pct=9.2))
            if d != missing:
                s.add(OMOTransaction(transaction_date=d, maturity_date=d, instrument="SDF", tenor_label="1D", tenor_days=1,
                                     accepted_bdt_crore=500.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))
        # history that began long ago, so the ledger is not excused as "possibly before our records"
        old = TODAY - datetime.timedelta(days=300)
        s.add(OMOTransaction(transaction_date=old, maturity_date=old, instrument="SDF", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=1.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))
        # the day after the missing one prints a maturity that only a deal on the missing day can explain
        s.add(OMOTransaction(transaction_date=nxt, maturity_date=nxt, instrument="AR", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=0.0, maturity_bdt_crore=3471.54, rate_pct=9.5, direction="INJECTION"))
        s.commit()
        return eng, s, missing

    def _issues(self, monkeypatch, eng):
        import validate
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        return validate.integrity_check(limit_per_rule=200)["issues"]

    def test_the_hole_fails_both_checks_until_it_is_recorded(self, monkeypatch):
        eng, s, missing = self._world()
        issues = self._issues(monkeypatch, eng)
        assert any(f"no OMO operation on working day {missing}" in i for i in issues), issues
        assert any("omo-ledger" in i and "AR" in i for i in issues), issues

    def test_once_recorded_neither_check_blames_us(self, monkeypatch):
        eng, s, missing = self._world()
        s.add(DataAbsence(dataset="omo", absent_date=missing, kind="bb_omission", reason="BB never posted it",
                          verified_utc=datetime.datetime.utcnow(), checks=1))
        s.commit()
        issues = self._issues(monkeypatch, eng)
        assert not any(f"no OMO operation on working day {missing}" in i for i in issues), issues
        assert not any("omo-ledger" in i and "AR" in i for i in issues), issues

    def test_a_different_unrecorded_day_still_fails(self, monkeypatch):
        """Recording one hole is not a switch that turns the check off."""
        eng, s, missing = self._world()
        other = [d for d in _working_days(8, TODAY) if d != missing][0]
        s.query(OMOTransaction).filter(OMOTransaction.transaction_date == other).delete()
        s.add(DataAbsence(dataset="omo", absent_date=missing, kind="bb_omission", reason="x",
                          verified_utc=datetime.datetime.utcnow(), checks=1))
        s.commit()
        issues = self._issues(monkeypatch, eng)
        assert any(f"no OMO operation on working day {other}" in i for i in issues), issues


class TestABBPrintErrorIsReportedNotHidden:
    """29-Oct-2026: BB's own 28-Oct release shows a 1-day AR tranche maturing 29-Oct that its 29-Oct release
    leaves out. The ledger check would fail forever; an evidenced entry turns that one gap into a notice,
    and nothing else - a different gap on the day, or a BB correction, is judged on its merits."""

    D1 = TODAY - datetime.timedelta(days=5)

    def _world(self, printed=1202.99):
        eng, s = _mem()
        old = TODAY - datetime.timedelta(days=300)
        s.add(OMOTransaction(transaction_date=old, maturity_date=old, instrument="SDF", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=1.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))
        d0 = self.D1 - datetime.timedelta(days=1)
        far = self.D1 - datetime.timedelta(days=14)
        # the 14-day tranche BB did print, and the 1-day one it left out
        s.add(OMOTransaction(transaction_date=far, maturity_date=self.D1, instrument="AR", tenor_label="14D", tenor_days=14,
                             accepted_bdt_crore=1198.62, maturity_bdt_crore=0.0, rate_pct=9.5, direction="INJECTION"))
        s.add(OMOTransaction(transaction_date=d0, maturity_date=self.D1, instrument="AR", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=309.88, maturity_bdt_crore=0.0, rate_pct=9.5, direction="INJECTION"))
        s.add(OMOTransaction(transaction_date=self.D1, maturity_date=self.D1, instrument="AR", tenor_label="14D", tenor_days=14,
                             accepted_bdt_crore=0.0, maturity_bdt_crore=printed, rate_pct=9.5, direction="INJECTION"))
        s.commit()
        return eng

    def _report(self, monkeypatch, eng, entries):
        import engines.absences as A
        import validate
        monkeypatch.setattr(A, "known_print_errors", lambda path=None: entries)
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        return validate.integrity_check(limit_per_rule=200)

    def _entry(self, amount=309.96):
        return [{"dataset": "omo", "instrument": "AR", "date": self.D1, "omitted_crore": amount, "reason": "BB omitted it"}]

    def test_without_an_entry_it_is_a_defect(self, monkeypatch):
        rep = self._report(monkeypatch, self._world(), [])
        assert any("omo-ledger" in i and "phantom tranche" in i for i in rep["issues"]), rep["issues"]

    def test_with_an_entry_it_is_a_visible_notice_not_a_defect(self, monkeypatch):
        rep = self._report(monkeypatch, self._world(), self._entry())
        assert not any("omo-ledger" in i for i in rep["issues"]), rep["issues"]
        assert any("omo-ledger" in n and "BB print error on record" in n for n in rep["notices"]), rep["notices"]

    def test_the_entry_covers_only_the_gap_it_describes(self, monkeypatch):
        """A recorded 100 cr omission must not excuse a 310 cr gap."""
        rep = self._report(monkeypatch, self._world(), self._entry(100.0))
        assert any("omo-ledger" in i for i in rep["issues"]), rep["issues"]

    def test_once_bb_corrects_its_print_nothing_is_reported(self, monkeypatch):
        rep = self._report(monkeypatch, self._world(printed=1512.95), self._entry())
        assert not any("omo-ledger" in x for x in rep["issues"] + rep["notices"]), (rep["issues"], rep["notices"])

    def test_the_shipped_entry_is_complete(self):
        from engines.absences import known_print_errors
        e = [x for x in known_print_errors() if x["instrument"] == "AR"]
        assert e and e[0]["date"] == datetime.date(2026, 10, 29) and e[0]["reason"]


class TestATenorBBsOwnMaturityPrintContradicts:
    """25-Oct-2026: BB printed "CB Repo 7-Days 15,861.75", then printed that same tranche maturing two days later
    (15,870.00 = principal + 2 days' profit at 9.50%). Stored as 7 days it was counted as outstanding for five days
    it no longer existed, and the ledger blamed the wrong day. The correction is arithmetic on BB's own figures."""

    DEAL = TODAY - datetime.timedelta(days=12)
    TRUE = TODAY - datetime.timedelta(days=10)
    STORED = TODAY - datetime.timedelta(days=5)

    def _world(self):
        eng, s = _mem()
        old = TODAY - datetime.timedelta(days=300)
        s.add(OMOTransaction(transaction_date=old, maturity_date=old, instrument="SDF", tenor_label="1D", tenor_days=1,
                             accepted_bdt_crore=1.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))
        s.add(OMOTransaction(transaction_date=self.DEAL, maturity_date=self.STORED, instrument="CB_REPO", tenor_label="7D",
                             tenor_days=7, accepted_bdt_crore=15861.75, maturity_bdt_crore=0.0, rate_pct=9.5,
                             direction="INJECTION"))
        # BB's later print of what matured: principal plus TWO days' profit
        s.add(OMOTransaction(transaction_date=self.TRUE, maturity_date=self.TRUE, instrument="CB_REPO", tenor_label="7D",
                             tenor_days=7, accepted_bdt_crore=0.0, maturity_bdt_crore=15870.00, rate_pct=9.5,
                             direction="INJECTION"))
        s.commit()
        return eng, s

    def _entries(self):
        return [{"dataset": "omo", "instrument": "CB_REPO", "deal_date": self.DEAL, "accepted_crore": 15861.75,
                 "true_maturity": self.TRUE, "reason": "BB printed its maturity two days later"}]

    def _issues(self, monkeypatch, eng):
        import validate
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        return [i for i in validate.integrity_check(limit_per_rule=200)["issues"] if i.startswith("omo-ledger")]

    def test_uncorrected_the_ledger_fails(self, monkeypatch):
        eng, s = self._world()
        assert self._issues(monkeypatch, eng), "stored as 7 days it disagrees with BB's own maturity print"

    def test_corrected_the_ledger_agrees_with_bb_on_both_days(self, monkeypatch):
        import engines.absences as A
        eng, s = self._world()
        monkeypatch.setattr(A, "known_tenor_corrections", lambda path=None: self._entries())
        done = A.sync_tenor_corrections(s)
        s.commit()
        assert len(done["corrected"]) == 1
        row = s.query(OMOTransaction).filter_by(instrument="CB_REPO", accepted_bdt_crore=15861.75).one()
        assert row.maturity_date == self.TRUE and row.tenor_days == 2
        assert row.tenor_label == "7D", "the tenor BB PRINTED stays what BB printed"
        assert self._issues(monkeypatch, eng) == []

    def test_a_reread_of_the_pdf_cannot_bring_the_printed_tenor_back(self):
        import engines.absences as A
        txn = {"instrument": "CB_REPO", "transaction_date": self.DEAL, "accepted_bdt_crore": 15861.75,
               "tenor_days": 7, "maturity_date": self.STORED, "tenor_label": "7D"}
        assert A.apply_tenor_correction(txn, self._entries()) is True
        assert txn["tenor_days"] == 2 and txn["maturity_date"] == self.TRUE
        assert A.apply_tenor_correction(txn, self._entries()) is False, "already corrected: nothing to do"

    def test_it_matches_one_tranche_not_every_deal_of_the_instrument(self):
        import engines.absences as A
        other = {"instrument": "CB_REPO", "transaction_date": self.DEAL, "accepted_bdt_crore": 9000.0,
                 "tenor_days": 7, "maturity_date": self.STORED}
        assert A.apply_tenor_correction(other, self._entries()) is False and other["tenor_days"] == 7

    def test_the_shipped_correction_is_complete_and_the_arithmetic_holds(self):
        from engines.absences import known_tenor_corrections
        e = [x for x in known_tenor_corrections() if x["instrument"] == "CB_REPO"]
        assert e and e[0]["deal_date"] == datetime.date(2026, 10, 25) and e[0]["true_maturity"] == datetime.date(2026, 10, 27)
        assert abs(e[0]["accepted_crore"] * (1 + 0.095 * 2 / 365) - 15870.00) < 0.05, "BB's printed maturity is 2 days' profit"
