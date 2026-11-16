"""The real incidents, replayed. Each test is something that went wrong in Oct-Nov 2026 and was fixed by a person reading logs; it
must now be handled by code, end to end, and the test says WHY it matters (Rule 9).
"""
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DataChange, InterbankRepo, PipelineRun, StepRun
from engines import doctor as DR
from engines import failures as F
from engines import step_ledger as SL
from engines.pipeline import replace_daily_rows
from fetchers.interbank_repo import parse_interbank_repo_html

D = datetime.date


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


# BB's November page as it was on 12-Nov-2026: 10-Nov, an internally inconsistent 11-Nov, and 12-Nov
_BB_PAGE = ("<table>"
            "<tr><td>10/11/2026</td><td>50</td><td>4000.5</td><td>1-7</td><td>8.80-8.90</td><td>8.85</td></tr>"
            "<tr><td>11/11/2026</td><td>59</td><td>5629.689717845</td><td>1-7</td><td>8.90-8.85</td><td>8.92</td></tr>"
            "<tr><td>12/11/2026</td><td>40</td><td>3000.0</td><td>1-7</td><td>8.80-8.90</td><td>8.86</td></tr>"
            "</table>")


class TestTheRepoIncidentEndToEnd:
    def test_one_bad_bb_row_no_longer_costs_the_good_days(self):
        """12-16 Nov: ONE row BB printed against itself aborted the whole month, so 10/12/15-Nov never arrived for four days and
        the banner said 'our fetch reports success'. From BB's HTML to the table, every day now lands."""
        eng, s = _mem()
        rows = parse_interbank_repo_html(_BB_PAGE)
        now = datetime.datetime(2026, 11, 12, 15, 0)
        for r in rows:
            r["ingested_utc"] = now
        st = replace_daily_rows(s, InterbankRepo, rows, ())
        s.commit()
        got = {r.trade_date: r for r in s.query(InterbankRepo).all()}
        assert sorted(got) == [D(2026, 11, 10), D(2026, 11, 11), D(2026, 11, 12)], "no day was lost"
        bad = got[D(2026, 11, 11)]
        assert bad.num_deals == 59 and bad.amount_crore == 5629.689717845, "the facts BB printed are kept"
        assert bad.rate_min_pct is None and bad.rate_max_pct is None and bad.war_pct is None, "the contradicted rates are blank"
        assert not st.get("rejected")


class TestTheStaleBannerIncident:
    def test_a_blocked_github_read_cannot_overwrite_the_pcs_failure_as_the_latest_row(self):
        """The PC recorded 32 CheckViolations; GitHub's blocked run read 0 rows, logged OK, and became the 'latest row'. In the ledger
        the PC's failure stays, the GitHub read is 'empty', and the step is NOT healthy."""
        eng, s = _mem()
        now = datetime.datetime(2026, 11, 15, 22, 0)
        SL.record_step_runs(s, ["interbank_repo: (psycopg2.errors.CheckViolation) violates check constraint \"ck_repo_rate_band\""],
                            {"interbank_repo": {"fetched": 30, "stored": 26}}, now - datetime.timedelta(hours=4), "pc")
        SL.record_step_runs(s, [], {"interbank_repo": {"fetched": 0, "stored": 0}}, now, "github")
        s.commit()
        known, healthy, bad = SL.step_health(s, "interbank_repo", now)
        assert known and not healthy and bad.error_class == F.DB_CONSTRAINT and bad.machine == "pc"
        gh = s.query(StepRun).filter_by(machine="github").one()
        assert gh.status == "empty", "a daily BB step that read nothing is never 'ok'"


class TestTheGuardianPageSpeaksPlainly:
    def test_a_tenor_repair_reads_as_a_sentence_with_its_proof_and_how_to_undo_it(self):
        from doctor_logic import doctor_sentence
        detail = json.dumps({"row_id": 7, "instrument": "AR", "deal_date": "2026-10-28", "accepted_crore": 309.88,
                             "old_tenor_days": 1, "old_maturity": "2026-10-29", "new_tenor_days": 14,
                             "new_maturity": "2026-11-11",
                             "proof": {"printed_crore": 440.6, "held_crore": 129.59, "gap_crore": 311.01,
                                       "reproduced_crore": 311.01, "rate_pct": 9.5}})
        text = doctor_sentence("tenor_autoheal", detail, 42)
        assert "tenor 1 -> 14 days" in text and "311.01" in text and "revert_tenor_autoheal(session, 42)" in text

    def test_a_lost_day_reads_with_its_evidence_and_other_records_pass_through(self):
        from doctor_logic import doctor_sentence
        d = json.dumps({"dataset": "fxrates", "day": "2026-11-05", "evidence": "Not read while the page described it."})
        assert "recorded as lost for good" in doctor_sentence("lost_day", d, 1)
        assert doctor_sentence("yield_issue_date", "2Y: moved", 1) == "2Y: moved"
        assert doctor_sentence("tenor_autoheal", "not json", 1) == "not json"


class TestTheDoctorItselfIsWatched:
    def _issues(self, monkeypatch, eng):
        import validate
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check(limit_per_rule=200)
        return [n for n in rep["notices"] if n.startswith("doctor")]

    def test_refreshes_recorded_but_no_doctor_for_six_hours_is_noticed(self, monkeypatch):
        """A repair system that silently stopped looks exactly like a healthy one."""
        eng, s = _mem()
        old = datetime.datetime.utcnow() - datetime.timedelta(hours=9)
        s.add(StepRun(run_utc=old, machine="pc", step="omo", status="ok"))
        s.add(StepRun(run_utc=old, machine="pc", step="doctor", status="ok"))
        s.add(StepRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(hours=1), machine="pc", step="omo", status="ok"))
        s.commit()
        n = self._issues(monkeypatch, eng)
        assert len(n) == 1 and "has not run for" in n[0]

    def test_a_recent_doctor_is_quiet_and_an_empty_ledger_is_not_a_false_alarm(self, monkeypatch):
        eng, s = _mem()
        assert self._issues(monkeypatch, eng) == [], "nothing recorded yet: no alarm"
        s.add(StepRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(minutes=20), machine="pc", step="omo", status="ok"))
        s.add(StepRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(minutes=5), machine="pc", step="doctor", status="ok"))
        s.commit()
        assert self._issues(monkeypatch, eng) == []


class TestEveryNewTableIsRegistered:
    def test_the_doctors_tables_are_declared_so_a_forgotten_table_fails_the_build(self):
        from engines import expectations as EX
        for t in ("step_runs", "quarantined_rows", "tenor_overrides"):
            assert t in EX.NOT_WATCHED and len(EX.NOT_WATCHED[t]) > 20
