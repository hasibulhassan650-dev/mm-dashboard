"""Doctor phase 3: it repairs only what has PROOF, records every change with its old values, and can be reverted.

The proofs are the real incidents, replayed as fixtures:
  * 25-Oct-2026 CB Repo 'Tenor 7-Days 15,861.75' matured 27-Oct (BB printed 15,870.00 = principal + 2 days' profit at 9.50%);
  * 28-Oct-2026 Assured Repo '1-Day 309.88' matured 11-Nov (BB printed 311.01 = 309.88 + 14 days' profit);
  * exchange-rate days 5-Nov and 10-Nov that no read landed on while BB's page (which keeps only its latest day) showed them.
A wrong automatic correction is the worst outcome, so the tests spend as much effort on what it must NOT do.
"""
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DataAbsence, DataChange, FxRateDaily, OMOTransaction, StepRun, TenorOverride
from engines import doctor as DR
from engines.absences import apply_tenor_correction, known_tenor_corrections

D = datetime.date


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def T(i, inst, deal, acc, rate, tenor, mat):
    return {"id": i, "instrument": inst, "deal_date": deal, "accepted": acc, "rate_pct": rate, "tenor_days": tenor,
            "maturity_date": mat}


# CB Repo: 15,861.75 dealt Sun 25-Oct, printed '7-Days' -> stored to mature Sun 1-Nov; BB printed 15,870.00 on Tue 27-Oct
CB = T(1, "CB_REPO", D(2026, 10, 25), 15861.75, 9.5, 7, D(2026, 11, 1))
# Assured Repo: 309.88 dealt 28-Oct printed '1-Day' -> stored 29-Oct; BB printed 311.01 on 11-Nov (with the 129.59 of a 7-day tranche)
AR_WRONG = T(2, "AR", D(2026, 10, 28), 309.88, 9.5, 1, D(2026, 10, 29))
AR_RIGHT = T(3, "AR", D(2026, 11, 4), 129.35, 9.5, 7, D(2026, 11, 11))


class TestATenorIsCorrectedOnlyWithProof:
    def test_the_25_oct_cb_repo_is_proved_a_two_day_tranche(self):
        got = DR.find_tenor_mismatches({("CB_REPO", D(2026, 10, 27)): 15870.00}, [CB])
        assert len(got) == 1 and got[0]["true_maturity"] == D(2026, 10, 27) and got[0]["true_tenor_days"] == 2
        assert got[0]["proof"]["gap_crore"] == 15870.0 and abs(got[0]["proof"]["reproduced_crore"] - 15870.01) < 0.011

    def test_the_28_oct_assured_repo_is_proved_a_fourteen_day_tranche_beside_a_correct_one(self):
        """BB printed 440.60 on 11-Nov: the 7-day tranche we hold explains 129.59, and the rest (311.01) is the 14-day one."""
        got = DR.find_tenor_mismatches({("AR", D(2026, 11, 11)): 440.60}, [AR_WRONG, AR_RIGHT])
        assert len(got) == 1 and got[0]["id"] == 2 and got[0]["true_tenor_days"] == 14

    def test_a_near_miss_is_not_corrected(self):
        """50 paise off is not a proof: a wrong correction is worse than a visible gap."""
        assert DR.find_tenor_mismatches({("CB_REPO", D(2026, 10, 27)): 15870.50}, [CB]) == []

    def test_two_tranches_that_both_fit_are_not_corrected(self):
        twin = dict(CB, id=9)
        assert DR.find_tenor_mismatches({("CB_REPO", D(2026, 10, 27)): 15870.00}, [CB, twin]) == []

    def test_a_tranche_claimed_by_two_different_days_is_trusted_by_neither(self):
        """If BB's prints on two days would each be explained by the same tranche, the arithmetic is coincidence, not proof."""
        printed = {("CB_REPO", D(2026, 10, 27)): 15870.00,
                   ("CB_REPO", D(2026, 10, 28)): 15861.75 * (1 + 0.095 * 3 / 365)}
        assert DR.find_tenor_mismatches(printed, [CB]) == []

    def test_a_gap_within_the_ledgers_tolerance_is_not_a_mismatch(self):
        assert DR.find_tenor_mismatches({("AR", D(2026, 11, 11)): 130.0}, [AR_RIGHT]) == []

    def test_mls_is_never_touched_it_matures_in_parts(self):
        mls = T(5, "MLS", D(2026, 8, 9), 1340.0, 3.0, 14, D(2026, 8, 23))
        assert DR.find_tenor_mismatches({("MLS", D(2026, 8, 25)): 1340.0 * (1 + 0.03 * 16 / 365)}, [mls]) == []

    def test_a_maturity_before_the_deal_is_not_a_tenor(self):
        assert DR.find_tenor_mismatches({("CB_REPO", D(2026, 10, 20)): 15870.00}, [CB]) == []


class TestTheDoctorWritesItDownAndCanUndoIt:
    def _world(self):
        eng, s = _mem()
        now = datetime.datetime(2026, 11, 3, 12, 0)
        s.add(OMOTransaction(transaction_date=CB["deal_date"], maturity_date=CB["maturity_date"], instrument="CB_REPO",
                             tenor_label="7D", tenor_days=7, accepted_bdt_crore=15861.75, maturity_bdt_crore=0.0,
                             rate_pct=9.5, direction="INJECTION"))
        s.add(OMOTransaction(transaction_date=D(2026, 10, 27), maturity_date=D(2026, 10, 27), instrument="CB_REPO",
                             tenor_label="7D", tenor_days=7, accepted_bdt_crore=0.0, maturity_bdt_crore=15870.00,
                             rate_pct=9.5, direction="INJECTION"))
        s.commit()
        return s, now

    def test_the_row_is_corrected_with_its_proof_and_old_values_on_the_record(self):
        s, now = self._world()
        applied = DR.heal_tenors(s, now)
        s.commit()
        row = s.query(OMOTransaction).filter(OMOTransaction.accepted_bdt_crore > 0).one()
        assert len(applied) == 1 and row.tenor_days == 2 and row.maturity_date == D(2026, 10, 27)
        assert row.tenor_label == "7D", "the tenor BB PRINTED stays what BB printed"
        ch = s.query(DataChange).filter_by(change_class="tenor_autoheal").one()
        d = json.loads(ch.detail)
        assert d["old_tenor_days"] == 7 and d["old_maturity"] == "2026-11-01" and d["proof"]["gap_crore"] == 15870.0

    def test_it_is_idempotent(self):
        s, now = self._world()
        DR.heal_tenors(s, now)
        s.commit()
        assert DR.heal_tenors(s, now) == []
        assert s.query(DataChange).filter_by(change_class="tenor_autoheal").count() == 1

    def test_a_reread_of_the_pdf_cannot_bring_the_printed_tenor_back(self):
        """The OMO fetch re-reads 35 days of PDFs every hour; the correction must be consulted at store time like a YAML one."""
        s, now = self._world()
        DR.heal_tenors(s, now)
        s.commit()
        txn = {"instrument": "CB_REPO", "transaction_date": CB["deal_date"], "accepted_bdt_crore": 15861.75, "tenor_days": 7,
               "maturity_date": D(2026, 11, 1)}
        assert apply_tenor_correction(txn, known_tenor_corrections(session=s)) is True
        assert txn["tenor_days"] == 2 and txn["maturity_date"] == D(2026, 10, 27)

    def test_a_correction_can_be_reverted_from_its_record(self):
        s, now = self._world()
        DR.heal_tenors(s, now)
        s.commit()
        cid = s.query(DataChange).filter_by(change_class="tenor_autoheal").one().id
        assert DR.revert_tenor_autoheal(s, cid) is True
        s.commit()
        row = s.query(OMOTransaction).filter(OMOTransaction.accepted_bdt_crore > 0).one()
        assert row.tenor_days == 7 and row.maturity_date == D(2026, 11, 1)
        assert s.query(TenorOverride).count() == 0
        assert s.query(DataChange).filter_by(change_class="tenor_autoheal_reverted").count() == 1

    def test_a_cap_stops_a_runaway(self):
        """If the proof ever had a bug the cap keeps it from rewriting the ledger wholesale."""
        eng, s = _mem()
        now = datetime.datetime(2026, 11, 3, 12, 0)
        for i in range(5):
            deal = D(2026, 10, 1) + datetime.timedelta(days=i)
            amt = 1000.0 + i * 777.77
            s.add(OMOTransaction(transaction_date=deal, maturity_date=deal + datetime.timedelta(days=30), instrument="AR",
                                 tenor_label="30D", tenor_days=30, accepted_bdt_crore=amt, maturity_bdt_crore=0.0,
                                 rate_pct=9.5, direction="INJECTION"))
            m = deal + datetime.timedelta(days=3)
            s.add(OMOTransaction(transaction_date=m, maturity_date=m, instrument="AR", tenor_label="1D", tenor_days=1,
                                 accepted_bdt_crore=0.0, maturity_bdt_crore=round(amt * (1 + 0.095 * 3 / 365), 2),
                                 rate_pct=9.5, direction="INJECTION"))
        s.commit()
        assert len(DR.heal_tenors(s, now, cap=2)) == 2


class TestALostDayIsRecordedFromOurOwnReadLog:
    HOL = set()

    def _reads(self, **kw):
        base = datetime.datetime(2026, 11, 1, 12, 0)
        return {D(2026, 11, 4): base + datetime.timedelta(days=6),      # read Sat 7-Nov
                D(2026, 11, 8): base + datetime.timedelta(days=8),      # read Mon 9-Nov
                D(2026, 11, 3): base + datetime.timedelta(days=3), **kw}

    def test_the_5_nov_hole_is_proved_unrecoverable(self):
        got = DR.find_lost_days(self._reads(), self.HOL)
        assert [g["day"] for g in got] == [D(2026, 11, 5)]
        assert got[0]["prev_day"] == D(2026, 11, 4) and got[0]["next_day"] == D(2026, 11, 8)

    def test_a_missing_day_at_the_tail_is_not_lost_yet(self):
        """The page may still be showing it: with no later day held nothing is concluded."""
        assert DR.find_lost_days({D(2026, 11, 3): datetime.datetime(2026, 11, 4), D(2026, 11, 4): datetime.datetime(2026, 11, 5)},
                                 self.HOL) == []

    def test_a_holiday_is_not_a_lost_day(self):
        assert DR.find_lost_days(self._reads(), {D(2026, 11, 5)}) == []

    def test_a_day_already_on_record_is_not_recorded_twice(self):
        assert DR.find_lost_days(self._reads(), self.HOL, already_recorded=[D(2026, 11, 5)]) == []

    def test_the_doctor_records_it_once_with_the_evidence(self):
        eng, s = _mem()
        for d, t in self._reads().items():
            s.add(FxRateDaily(rate_date=d, currency="USD", ingested_utc=t))
        s.commit()
        now = datetime.datetime(2026, 11, 16, 12, 0)
        first = DR.heal_lost_days(s, now)
        s.commit()
        assert first == ["fxrates 2026-11-05 (lost)"]
        a = s.query(DataAbsence).filter_by(dataset="fxrates", absent_date=D(2026, 11, 5)).one()
        assert a.kind == "lost" and "cannot be re-read" in a.reason and "Doctor" in a.reason
        assert DR.heal_lost_days(s, now) == [], "idempotent"


class TestTheDoctorLeavesAHeartbeat:
    def test_a_run_writes_a_doctor_step_row(self):
        eng, s = _mem()
        out = DR.run_doctor(s, datetime.datetime(2026, 11, 16, 12, 0))
        s.commit()
        row = s.query(StepRun).filter_by(step="doctor").one()
        assert row.status == "ok" and out["errors"] == []

    def test_a_repair_that_raises_is_reported_and_never_breaks_the_run(self, monkeypatch):
        eng, s = _mem()
        monkeypatch.setattr(DR, "heal_tenors", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        out = DR.run_doctor(s, datetime.datetime(2026, 11, 16, 12, 0))
        s.commit()
        assert out["errors"] and out["errors"][0].startswith("doctor:")
        assert s.query(StepRun).filter_by(step="doctor").one().status == "failed"
