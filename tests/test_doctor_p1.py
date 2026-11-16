"""Doctor phase 1: one ledger, one classifier.

The repo incident (12-16 Nov 2026): a CheckViolation was recorded 32 times and nobody read it, and a blocked GitHub run that read
zero rows and logged 'OK' overwrote it as the 'latest row'. The classifier names the failure from its evidence; the ledger keeps
every machine's every step; and the freshness rule is judged from the ledger, with the diagnosis in the message.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import InterbankRepo, PipelineRun, StepRun
from engines import failures as F
from engines import step_ledger as SL

NOW = datetime.datetime(2026, 11, 16, 12, 0, 0)

# the real text of the 31-fold repo error (abridged where Postgres repeats itself)
REPO_ERROR = ('interbank_repo: (raised as a result of Query-invoked autoflush; consider using a session.no_autoflush block if this '
              'flush is occurring prematurely)\n(psycopg2.errors.CheckViolation) new row for relation "interbank_repo" violates check '
              'constraint "ck_repo_rate_band"\nDETAIL:  Failing row contains (90, 2026-11-11, 59, 5629.689717845, 1, 7, 8.85, 8.9, 8.92')


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


class TestTheClassifierNamesTheFailureFromItsEvidence:
    def test_the_repo_incident_is_a_db_constraint_failure(self):
        assert F.classify(REPO_ERROR, "interbank_repo") == F.DB_CONSTRAINT

    def test_a_listed_pdf_bb_does_not_serve(self):
        assert F.classify("pr14381: download failed (RuntimeError)") == F.PDF_UNSERVED
        assert F.classify("<html><title>Request Rejected</title>") == F.PDF_UNSERVED

    def test_a_challenge_page_is_a_block_not_nothing_published(self):
        assert F.classify("BB answered November, 2026 with a block/challenge page, not the table") == F.BB_BLOCKED
        assert F.classify("F5 CAPTCHA returned - challenge may have rotated") == F.BB_BLOCKED

    def test_maintenance_and_server_errors_are_bb_down(self):
        assert F.classify("BB is under maintenance") == F.BB_DOWN and F.classify("HTTP 503") == F.BB_DOWN

    def test_a_page_that_changed_is_a_parse_degradation(self):
        assert F.classify("BB's page changed AND the parse got worse - parsed 5 rows, less than half the 19 last seen") == F.PARSE_DEGRADED

    def test_zero_rows_is_empty_until_proven_blocked(self):
        assert F.classify("interbank_repo: no rows were read from BB's page (blocked, or the page changed)") == F.EMPTY

    def test_timeouts_are_the_network_not_bb(self):
        assert F.classify("ReadTimeout: HTTPSConnectionPool(host='www.bb.org.bd') Read timed out") == F.NETWORK

    def test_text_nobody_has_taught_us_is_unknown_and_empty_text_is_not_a_failure(self):
        assert F.classify("something new went wrong") == F.UNKNOWN and F.classify("") == F.UNKNOWN and F.classify(None) == F.UNKNOWN

    def test_a_constraint_violation_wins_over_a_block_word_in_the_same_text(self):
        """Order matters: the failing row is the cause; a stray 'blocked' in the message must not hide it."""
        assert F.classify("CheckViolation ... the request was blocked by the pooler") == F.DB_CONSTRAINT


class TestEveryClassHasAnOwnerAndAPolicy:
    def test_all_classes_have_a_policy(self):
        for cls in (F.DB_CONSTRAINT, F.BB_BLOCKED, F.BB_DOWN, F.PDF_UNSERVED, F.PARSE_DEGRADED, F.EMPTY, F.NETWORK, F.UNKNOWN):
            p = F.policy(cls)
            assert p.owner in ("bb", "us", "environment", "unknown") and p.grace_days >= 0

    def test_a_db_constraint_failure_has_a_playbook_and_no_waiting(self):
        p = F.policy(F.DB_CONSTRAINT)
        assert p.playbook == "isolate_and_blank" and p.grace_days == 0 and p.owner == "bb"

    def test_the_diagnosis_keeps_the_part_of_a_constraint_error_that_names_the_cause(self):
        line = F.diagnosis("interbank_repo", F.DB_CONSTRAINT, "pc", NOW, REPO_ERROR)
        assert "ck_repo_rate_band" in line and "pc" in line and "BB's side" in line


class TestTheLedgerKeepsEveryMachinesEveryStep:
    def test_a_failure_empty_read_and_clean_step_get_their_own_status(self):
        rows = {r["step"]: r for r in SL.build_rows(
            [REPO_ERROR, "callmoney: BB answered a block/challenge page"], {"omo": {"fetched": 40, "stored": 2},
                                                                            "refrate": {"fetched": 0, "stored": 0}}, NOW, "pc")}
        assert rows["interbank_repo"]["status"] == "failed" and rows["interbank_repo"]["error_class"] == F.DB_CONSTRAINT
        assert rows["callmoney"]["status"] == "blocked"
        assert rows["omo"]["status"] == "ok" and rows["omo"]["rows_fetched"] == 40
        assert rows["refrate"]["status"] == "empty", "a daily BB step that read ZERO rows is never 'ok'"

    def test_a_step_that_reported_nothing_is_not_recorded_as_ok(self):
        assert SL.build_rows([], {}, NOW, "pc") == []

    def test_error_lines_are_attributed_by_their_step_prefix(self):
        assert SL.step_of("omo: boom") == "omo" and SL.step_of("POLICY RATE CHANGE: repo 9->10") == "other"


class TestAStepIsJudgedAcrossBothMachines:
    def _add(self, s, machine, status, hours_ago, step="interbank_repo", cls=None):
        s.add(StepRun(run_utc=NOW - datetime.timedelta(hours=hours_ago), machine=machine, step=step, status=status,
                      error_class=cls, error="x" if status != "ok" else None))
        s.commit()

    def test_the_repo_incident_a_blocked_github_read_cannot_hide_the_pcs_failure(self):
        """The PC failed (CheckViolation); GitHub then read zero rows and logged OK and became the 'latest row'."""
        eng, s = _mem()
        self._add(s, "pc", "failed", 4, cls=F.DB_CONSTRAINT)
        self._add(s, "github", "empty", 1, cls=F.EMPTY)
        known, healthy, bad = SL.step_health(s, "interbank_repo", NOW)
        assert known and not healthy and bad.machine == "pc" and bad.error_class == F.DB_CONSTRAINT

    def test_a_clean_pc_read_after_a_failure_is_healthy_again(self):
        eng, s = _mem()
        self._add(s, "pc", "failed", 5, cls=F.DB_CONSTRAINT)
        self._add(s, "pc", "ok", 1)
        assert SL.step_health(s, "interbank_repo", NOW)[1] is True

    def test_githubs_routine_empty_read_does_not_spoil_a_healthy_pc(self):
        """BB's firewall blocks GitHub as a matter of course; that says nothing against the step."""
        eng, s = _mem()
        self._add(s, "pc", "ok", 3)
        self._add(s, "github", "empty", 1, cls=F.EMPTY)
        assert SL.step_health(s, "interbank_repo", NOW)[1] is True

    def test_a_failure_after_an_ok_makes_the_step_unhealthy(self):
        eng, s = _mem()
        self._add(s, "pc", "ok", 5)
        self._add(s, "pc", "failed", 1, cls=F.DB_CONSTRAINT)
        assert SL.step_health(s, "interbank_repo", NOW)[1] is False

    def test_an_unknown_step_falls_back_to_the_old_rule(self):
        eng, s = _mem()
        assert SL.step_health(s, "never_ran", NOW) == (False, False, None)

    def test_old_rows_outside_the_window_do_not_count(self):
        eng, s = _mem()
        self._add(s, "pc", "ok", 30)
        assert SL.step_health(s, "interbank_repo", NOW)[0] is False


class TestTheEscalationCarriesItsCause:
    def test_a_stale_repo_with_a_recorded_constraint_failure_says_so(self, monkeypatch):
        """The 16-Nov banner said 'our fetch reports success ... succeeding at fetching nothing' while the cause (a CheckViolation
        on one BB row) sat in the table. The message now names the class, the machine and the constraint."""
        import validate
        eng, s = _mem()
        today = datetime.date.today()
        s.add(InterbankRepo(trade_date=today - datetime.timedelta(days=14), num_deals=1, amount_crore=1.0))
        s.add(PipelineRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(hours=1), kind="refresh", errors="[]"))
        s.add(StepRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(hours=2), machine="pc", step="interbank_repo",
                      status="failed", error_class=F.DB_CONSTRAINT, error=REPO_ERROR))
        s.add(StepRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(hours=1), machine="github", step="interbank_repo",
                      status="empty", error_class=F.EMPTY))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check(limit_per_rule=200)
        lines = [i for i in rep["issues"] + rep["waiting"] if "repo" in i and i.startswith("freshness")]
        assert lines, rep["issues"] + rep["waiting"]
        msg = lines[0]
        assert "DB_CONSTRAINT" in msg and "ck_repo_rate_band" in msg and "pc" in msg
        assert "reports success" not in msg, "a recorded failure must not be called a success"
