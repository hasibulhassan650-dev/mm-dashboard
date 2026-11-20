"""Doctor leftovers: a failure is an EPISODE with an age and an owner, and the alert issue must not close while one is open.

The harms these tests name:
  * the data-integrity issue auto-closed because the DATA checks were clean while the repo step had been failing for days
    (the 12-16 Nov incident: clean-looking data, a CheckViolation recorded 32 times and read by nobody);
  * the Guardian said 'waiting 45d' for a failure whose current episode was 3 days old (an age measured from the TEXT's first
    appearance, not from the failure), so the desk could not tell a stuck feed from a fresh one;
  * a blocked GitHub run, which logs 'empty' as a matter of course, started or broke a PC failure streak.
"""
import datetime
import json
import os
import re
import sys
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DataAbsence, DataIssue, PipelineRun, StepRun
from engines import failures as F
from engines import step_ledger as SL
from engines.guardian import record

NOW = datetime.datetime(2026, 11, 20, 12, 0, 0)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _row(machine, status, hours_ago, step="interbank_repo", cls=None, error=None):
    return SimpleNamespace(run_utc=NOW - datetime.timedelta(hours=hours_ago), machine=machine, step=step, status=status,
                           error_class=cls, error=error if error is not None else ("x" if status != "ok" else None))


def _add(s, machine, status, hours_ago, step="interbank_repo", cls=None, error=None):
    s.add(StepRun(run_utc=NOW - datetime.timedelta(hours=hours_ago), machine=machine, step=step, status=status,
                  error_class=cls, error=error if error is not None else ("x" if status != "ok" else None)))
    s.commit()


class TestCurrentStreak:
    def test_github_blocked_run_neither_starts_nor_resets_a_pc_streak(self):
        """GitHub is CAPTCHAd by BB as a matter of course. Its 'empty'/'blocked' reads say nothing about the step: they must not
        start an episode, and must not break the PC's real one (the 16-Nov race, in the ledger's own terms)."""
        rows = [_row("github", "blocked", 50, cls=F.BB_BLOCKED),              # before the PC failed: starts nothing
                _row("pc", "failed", 40, cls=F.DB_CONSTRAINT),
                _row("github", "empty", 30, cls=F.EMPTY),                      # in the middle: breaks nothing
                _row("pc", "failed", 20, cls=F.DB_CONSTRAINT),
                _row("github", "blocked", 1, cls=F.BB_BLOCKED)]
        st = SL.current_streak(rows, NOW)
        assert st["first_failed_utc"] == NOW - datetime.timedelta(hours=40)
        assert st["attempts"] == 2 and st["class"] == F.DB_CONSTRAINT and st["machine"] == "pc"
        # and with only GitHub's blocked reads there is no episode at all
        assert SL.current_streak([_row("github", "blocked", 5), _row("github", "empty", 2)], NOW) is None

    def test_an_ok_run_ends_the_streak_and_the_next_failure_starts_a_new_one(self):
        rows = [_row("pc", "failed", 30), _row("pc", "ok", 20), _row("pc", "failed", 5), _row("pc", "failed", 2)]
        st = SL.current_streak(rows, NOW)
        assert st["first_failed_utc"] == NOW - datetime.timedelta(hours=5) and st["attempts"] == 2 and not st["truncated"]

    def test_a_step_that_is_healthy_now_has_no_streak(self):
        assert SL.current_streak([_row("pc", "failed", 9), _row("pc", "ok", 1)], NOW) is None

    def test_a_streak_that_reaches_the_retention_edge_is_truncated(self):
        edge = SL.RETENTION_DAYS * 24
        assert SL.current_streak([_row("pc", "failed", edge - 1), _row("pc", "failed", 1)], NOW)["truncated"] is True
        # a failure that began 3 days ago is not 'at least' anything
        assert SL.current_streak([_row("pc", "failed", 72), _row("pc", "failed", 1)], NOW)["truncated"] is False
        # a streak that was ENDED by an ok and then restarted is not truncated even if old rows exist
        assert SL.current_streak([_row("pc", "failed", edge - 1), _row("pc", "ok", edge - 5), _row("pc", "failed", 1)],
                                 NOW)["truncated"] is False

    def test_when_the_pc_has_gone_quiet_githubs_failures_count(self):
        """step_health's rule: GitHub's rows stand in only when the PC has none recent."""
        rows = [_row("pc", "ok", 200), _row("github", "failed", 30), _row("github", "failed", 2)]
        assert SL.current_streak(rows, NOW)["attempts"] == 2


class TestOwnerActionRegistry:
    def test_every_failure_class_has_an_owner_action(self):
        classes = {v for k, v in vars(F).items() if k.isupper() and isinstance(v, str) and v == k}
        assert classes == set(F.CLASS_POLICY), "a class was added to failures.py without a policy"
        assert set(F.OWNER_ACTION) == set(F.CLASS_POLICY), "a class has no owner action: the issue would ask for nothing"

    def test_bb_owned_classes_ask_the_owner_to_do_nothing_and_unknown_ones_ask_for_something(self):
        for cls in (F.DB_CONSTRAINT, F.BB_DOWN, F.PDF_UNSERVED):
            assert F.OWNER_ACTION[cls].startswith("nothing: BB side")
        for cls in (F.NETWORK, F.BB_BLOCKED):
            assert F.OWNER_ACTION[cls].startswith("nothing unless the PC refresh also fails")
        for cls in (F.UNKNOWN, F.PARSE_DEGRADED):
            assert "Claude Code" in F.OWNER_ACTION[cls] and not F.OWNER_ACTION[cls].startswith("nothing")

    def test_diagnosis_step_reads_back_what_diagnosis_writes(self):
        line = "freshness: repo: stale [" + F.diagnosis("interbank_repo", F.DB_CONSTRAINT, "pc", NOW, "boom") + "]"
        assert F.diagnosis_step(line) == "interbank_repo" and F.diagnosis_step("freshness: repo: stale") is None

    @staticmethod
    def _weekly_problems(src: str) -> list:
        """What is wrong with weekly_fetch's step registry, as readable lines (empty when it is sound)."""
        bad = []
        settled = re.findall(r'_settle\("([A-Za-z_\-]+)"', src)
        if len(settled) != len(set(settled)):
            bad.append(f"a step is settled twice: {sorted(s for s in set(settled) if settled.count(s) > 1)}")
        if set(settled) != SL.REPORTING_STEPS - {"doctor"}:       # the Doctor writes its own heartbeat row
            bad.append(f"settled steps differ from REPORTING_STEPS: {sorted(set(settled) ^ (SL.REPORTING_STEPS - {'doctor'}))}")
        if src.count("_m = len(errors)") != len(settled):
            bad.append("a step takes its error mark without settling (or settles without one)")
        # every numbered step banner must be followed by a settle before the next banner
        for chunk in src.split('log.info("--- Step')[1:]:
            if "_settle(" not in chunk and "SKIPPED" not in chunk.split("\n")[0]:     # the light-mode 'skipped' notice is not a step
                bad.append(f"step banner without a settle: Step{chunk[:40]!r}")
        # a step that files errors under a name of its own must have that name in the registry (errors appended AFTER the ledger
        # is written, such as the guardian's, are not step outcomes)
        for p in set(re.findall(r'errors\.append\(f"([A-Za-z_\-]+):', src.split("import record_step_runs")[0])):
            if p not in SL.REPORTING_STEPS:
                bad.append(f"errors filed under '{p}:' which is not a registered step")
        return bad

    def test_every_step_weekly_fetch_runs_writes_a_row_so_its_failure_can_be_seen_to_end(self):
        """A step that wrote a row only when it errored (remittance, reserves ...) could never be seen to recover and would hold the
        data-integrity alert open for the whole 30-day retention. Adding a step to weekly_fetch without settling it must fail."""
        src = open(os.path.join(_ROOT, "weekly_fetch.py"), encoding="utf-8").read()
        assert self._weekly_problems(src) == []
        assert SL.DAILY_BB_STEPS <= SL.REPORTING_STEPS
        noted = set(re.findall(r'note_step\("([A-Za-z_\-]+)"', src))
        assert noted and noted <= SL.REPORTING_STEPS, noted - SL.REPORTING_STEPS

    def test_the_registry_guard_can_fail(self):
        """A guard that cannot fail is wrong: drop a settle, add an unsettled step, or file errors under a new name and it must say so."""
        src = open(os.path.join(_ROOT, "weekly_fetch.py"), encoding="utf-8").read()
        assert self._weekly_problems(src.replace('_settle("remittance", errors, _m)', "pass")), "dropped settle not caught"
        new_step = src.replace('    # ── Summary ──', '    log.info("--- Step 99: A new feed ---")\n    errors.append(f"newfeed: x")\n    # ── Summary ──')
        assert new_step != src and self._weekly_problems(new_step), "a new unsettled step was not caught"
        assert self._weekly_problems(src.replace('errors.append(f"remittance: {exc}")', 'errors.append(f"remit: {exc}")'))


class TestOpenFailures:
    def test_pc_healthy_step_does_not_hold_issue_open(self):
        """GitHub's blocked read arrives AFTER the PC's clean one; that is BB's firewall, not a failing step."""
        eng, s = _mem()
        _add(s, "pc", "failed", 6, cls=F.DB_CONSTRAINT)
        _add(s, "pc", "ok", 3)
        _add(s, "github", "blocked", 1, cls=F.BB_BLOCKED)
        assert SL.open_failures(s, NOW) == []

    def test_a_failing_step_is_listed_with_its_class_owner_action_and_what_the_doctor_tried(self):
        eng, s = _mem()
        _add(s, "pc", "failed", 30, step="omo", cls=F.PARSE_DEGRADED, error="page changed AND the parse got worse")
        _add(s, "pc", "failed", 2, step="omo", cls=F.PARSE_DEGRADED, error="page changed AND the parse got worse")
        _add(s, "pc", "ok", 1, step="doctor")
        out = SL.open_failures(s, NOW)
        assert len(out) == 1
        f = out[0]
        assert f["step"] == "omo" and f["class"] == F.PARSE_DEGRADED and f["attempts"] == 2
        assert f["owner_action"] == F.OWNER_ACTION[F.PARSE_DEGRADED]
        assert any("no automatic repair" in t for t in f["tried"])
        assert any("the Doctor last ran" in t for t in f["tried"])

    def test_a_monthly_step_is_open_while_failing_and_closes_on_its_next_clean_run(self):
        """remittance/reserves/policy/... used to write a row only on error: the failure was invisible to the alert (excluded) or,
        if included, could never end. Now every run writes a row, so a clean run is what closes it."""
        eng, s = _mem()
        _add(s, "pc", "failed", 40, step="remittance", cls=F.UNKNOWN)
        assert [f["step"] for f in SL.open_failures(s, NOW)] == ["remittance"]
        _add(s, "pc", "ok", 1, step="remittance")
        assert SL.open_failures(s, NOW) == []

    def test_a_row_under_a_name_weekly_fetch_never_settles_cannot_hold_the_issue_open(self):
        """Legacy rows / a stray error line ('other', 'holiday_calendar') have no ok row to end them."""
        eng, s = _mem()
        _add(s, "pc", "failed", 40, step="other", cls=F.UNKNOWN)
        assert SL.open_failures(s, NOW) == []

    def test_a_healthy_ledger_of_ok_rows_loads_nothing_and_lists_nothing(self):
        eng, s = _mem()
        for h in range(1, 40):
            for step in ("remittance", "omo", "doctor"):
                _add(s, "pc", "ok", h, step=step)
        assert SL.open_failures(s, NOW) == []


class TestEveryStepRecordsARow:
    def _run(self, steps, errors, step_errors=None):
        import weekly_fetch as W
        W.STEP_STATS.clear()
        W.STEP_ERRORS.clear()
        for name, fn in steps:
            mark = len(errors)
            fn(errors)
            W._settle(name, errors, mark)
        return {r["step"]: r for r in SL.build_rows(errors, W.STEP_STATS, NOW, "pc", W.STEP_ERRORS)}

    def test_a_clean_monthly_step_is_ok_with_unknown_counts_never_empty(self):
        """reserves/monetary/remittance legitimately read nothing on most days: 0 rows is 'ok' for them, 'empty' only for a daily BB feed."""
        import weekly_fetch as W
        rows = self._run([("circulars", lambda e: W.note_step("circulars", 0, 0)),
                          ("reserves", lambda e: None),
                          ("callmoney", lambda e: W.note_step("callmoney", 0, 0))], [])
        assert rows["circulars"]["status"] == "ok" and rows["reserves"]["status"] == "ok"
        assert rows["reserves"]["rows_fetched"] is None, "unknown counts are None, never a made-up 0"
        assert rows["callmoney"]["status"] == "empty" and rows["callmoney"]["error_class"] == F.EMPTY

    def test_a_failed_step_is_failed_and_its_neighbours_stay_ok(self):
        rows = self._run([("remittance", lambda e: e.append("remittance: boom")), ("reserves", lambda e: None)], [])
        assert rows["remittance"]["status"] == "failed" and rows["reserves"]["status"] == "ok"

    def test_lines_a_runner_prefixes_with_its_own_name_are_filed_under_the_step_that_ran(self):
        """The holiday fetcher says 'holiday_calendar: ...' and the circular watch says 'calendar_decisions.yaml entry 3: ...'. Filed by
        prefix they land on names that never write an ok row, so they could never end; filed by block they recover with 'holidays'."""
        rows = self._run([("holidays", lambda e: e.append("holiday_calendar: 2 rows refused")),
                          ("circulars", lambda e: e.append("calendar_decisions.yaml entry 3: needs decision"))], [])
        assert rows["holidays"]["status"] == "failed" and rows["circulars"]["status"] == "failed"
        assert "holiday_calendar" not in rows and "other" not in rows

    def test_a_line_that_names_another_known_step_stays_with_that_step(self):
        """A CheckViolation for the repo must stay on 'interbank_repo' wherever in the run it was raised: that is the 12-16 Nov incident."""
        rows = self._run([("pipeline", lambda e: e.append("interbank_repo: CheckViolation ck_repo_rate_band")),
                          ("interbank_repo", lambda e: __import__("weekly_fetch").note_step("interbank_repo", 5, 5))], [])
        assert rows["interbank_repo"]["status"] == "failed" and rows["pipeline"]["status"] == "ok"

    def test_a_failure_is_closed_by_the_next_runs_clean_row_end_to_end(self):
        eng, s = _mem()
        SL.record_step_runs(s, ["policy: policy fetch returned nothing (page unparseable)"], {}, NOW - datetime.timedelta(hours=3), "pc",
                            step_errors={"policy": ["policy: policy fetch returned nothing (page unparseable)"]})
        s.commit()
        assert [f["step"] for f in SL.open_failures(s, NOW)] == ["policy"]
        SL.record_step_runs(s, [], {"policy": {"fetched": 1, "stored": 0, "rejected": None}}, NOW - datetime.timedelta(hours=1), "pc",
                            step_errors={"policy": []})
        s.commit()
        assert SL.open_failures(s, NOW) == []

    def test_error_lines_that_no_block_claimed_still_fall_back_to_their_prefix(self):
        rows = SL.build_rows(["doctor: lost-day repair failed: x"], {}, NOW, "pc", {})
        assert [(r["step"], r["status"]) for r in rows] == [("doctor", "failed")]

class TestKnownAbsences:
    def test_a_recorded_known_absence_does_not_hold_the_issue_open(self):
        """BB published nothing and we hold the evidence: an empty read on those days is the explained hole, not a failure."""
        eng, s = _mem()
        _add(s, "pc", "empty", 30, step="omo", cls=F.EMPTY)
        _add(s, "pc", "empty", 2, step="omo", cls=F.EMPTY)
        assert [f["step"] for f in SL.open_failures(s, NOW)] == ["omo"]
        s.add(DataAbsence(dataset="omo", absent_date=(NOW - datetime.timedelta(hours=10)).date(), kind="bb_omission",
                          reason="BB posted no release (test)"))
        s.commit()
        assert SL.open_failures(s, NOW) == []

    def test_a_known_absence_never_excuses_a_failure_we_could_fix(self):
        eng, s = _mem()
        _add(s, "pc", "failed", 2, step="omo", cls=F.DB_CONSTRAINT)
        s.add(DataAbsence(dataset="omo", absent_date=(NOW - datetime.timedelta(hours=2)).date(), kind="bb_omission", reason="x"))
        s.commit()
        assert [f["step"] for f in SL.open_failures(s, NOW)] == ["omo"]


class TestTheIssueGate:
    def _clean_report(self, monkeypatch, eng):
        import validate
        s = sessionmaker(bind=eng)()
        s.add(PipelineRun(run_utc=datetime.datetime.utcnow() - datetime.timedelta(hours=1), kind="refresh", errors="[]",
                          quality=json.dumps({"ok": True, "ledger": {"opened": 0}})))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        # A near-empty test database fails unrelated data checks (calendar, freshness), so "the data checks are clean" is set
        # explicitly: what is under test is the ledger fields validate adds and the gate's decision, not those checks.
        return {**validate.integrity_check(), "ok": True}

    def test_issue_stays_open_while_a_class_is_open_though_integrity_is_clean(self, monkeypatch):
        """The data checks were clean the whole time the repo step was failing: 'ok' alone must not close the alert."""
        eng, s = _mem()
        now = datetime.datetime.utcnow()
        s.add(StepRun(run_utc=now - datetime.timedelta(hours=3), machine="pc", step="interbank_repo", status="failed",
                      error_class=F.DB_CONSTRAINT, error="violates check constraint ck_repo_rate_band"))
        s.commit()
        rep = self._clean_report(monkeypatch, eng)
        assert rep["ok"] is True, rep["issues"]
        assert [f["step"] for f in rep["open_failures"]] == ["interbank_repo"]
        assert SL.may_close(rep) is False
        assert "interbank_repo" in SL.render_bundle(rep["open_failures"])

    def test_the_issue_may_close_when_integrity_is_clean_and_nothing_is_open(self, monkeypatch):
        eng, s = _mem()
        rep = self._clean_report(monkeypatch, eng)
        assert rep["ok"] is True and rep["open_failures"] == [] and SL.may_close(rep) is True

    def test_unreadable_ledger_never_closes_the_issue(self, monkeypatch):
        """step_runs could not be read: that is 'unknown', and unknown must never look like 'nothing open'."""
        eng, s = _mem()
        monkeypatch.setattr(SL, "open_failures", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such table: step_runs")))
        rep = self._clean_report(monkeypatch, eng)
        assert rep["ok"] is True
        assert rep["open_failures"] is None and "no such table" in rep["open_failures_error"]
        assert SL.may_close(rep) is False
        assert SL.may_close({"ok": True}) is False, "a verdict without the field is not a clean ledger either"

    def test_a_failing_integrity_check_never_closes_whatever_the_ledger_says(self):
        assert SL.may_close({"ok": False, "open_failures": []}) is False

    def test_the_gate_closes_only_through_may_close(self):
        """The shell is the last place the rule can be bypassed: the close must sit behind may_close, not behind ok alone."""
        raw = open(os.path.join(_ROOT, ".github", "actions", "integrity-gate", "action.yml"), encoding="utf-8").read()
        assert "may_close" in raw
        before_close = raw[:raw.index("gh issue close")]
        # the condition enclosing the close (the one before the `-n "$n"` check) is the may_close result, never the bare ok
        assert before_close.rstrip().rsplit("if [", 2)[1].startswith(' "$can_close" = "True" ]')
        assert "can_close=$(python -c" in before_close and "may_close" in before_close


class TestBundle:
    def _f(self, **kw):
        base = {"step": "omo", "class": F.UNKNOWN, "owner": "unknown", "machine": "pc", "first_failed_utc": "2026-11-18 03:00:00",
                "last_failed_utc": "2026-11-20 11:00:00", "attempts": 4, "truncated": False,
                "owner_action": F.OWNER_ACTION[F.UNKNOWN], "tried": ["no automatic repair exists for this class"],
                "last_error": "boom"}
        base.update(kw)
        return base

    def test_bundle_redacts_connection_strings(self):
        """The repo is PUBLIC: a connection string in an error must never reach an issue body."""
        secret = "postgresql://postgres.abc:S3cr3tPw@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
        out = SL.render_bundle([self._f(last_error=f"OperationalError: could not connect using {secret} (timeout)")])
        assert "S3cr3tPw" not in out and "supabase.com" not in out and "postgres://" not in out and "[redacted]" in out
        assert "S3cr3tPw" not in SL.render_bundle([self._f(last_error="x postgresql+psycopg2://u:S3cr3tPw@h/db")])
        assert "S3cr3tPw" not in SL.redact(secret)

    def test_bundle_is_cut_to_githubs_body_limit(self):
        many = [self._f(step=f"s{i}", last_error="e" * 600) for i in range(400)]
        out = SL.render_bundle(many)
        assert len(out) <= SL.BUNDLE_LIMIT < 65536 and "cut" in out

    def test_a_truncated_streak_says_at_least_and_an_empty_list_renders_nothing(self):
        assert "since at least 2026-11-18" in SL.render_bundle([self._f(truncated=True)])
        assert "since 2026-11-18" in SL.render_bundle([self._f()])
        assert SL.render_bundle([]) == ""


class TestGuardianAgesAFailureFromItsEpisode:
    def _line(self, step="omo"):
        return (f"freshness: {step}: nothing newer than 2026-11-01 [last failure of {step}: UNKNOWN (cause not yet known) on pc "
                f"at 2026-11-19 10:00 - boom]")

    def _report(self, line):
        return {"issues": [], "waiting": [line], "by_table": {}, "by_table_waiting": {}}

    def test_age_restarts_after_a_good_run(self):
        """'waiting 45d' was the age of the fingerprint, which survives a recovery. The failure has been going for 2 days."""
        eng, s = _mem()
        long_ago = NOW - datetime.timedelta(days=20)
        record(s, self._report(self._line()), now=long_ago)     # first sighting, before any step_runs row exists
        s.commit()
        for hours, status in ((24 * 20, "failed"), (24 * 10, "ok"), (48, "failed"), (1, "failed")):
            s.add(StepRun(run_utc=NOW - datetime.timedelta(hours=hours), machine="pc", step="omo", status=status,
                          error_class=F.UNKNOWN if status == "failed" else None, error="boom" if status == "failed" else None))
        s.commit()
        record(s, self._report(self._line()), now=NOW)
        s.commit()
        row = s.query(DataIssue).one()
        assert row.first_seen == NOW - datetime.timedelta(hours=48), "the age must be the current episode's, not the text's"
        assert "since at least" not in row.message

    def test_an_issue_without_a_diagnosis_keeps_its_fingerprint_age(self):
        eng, s = _mem()
        first = NOW - datetime.timedelta(days=5)
        record(s, self._report("freshness: omo: nothing newer than 2026-11-01"), now=first)
        s.add(StepRun(run_utc=NOW - datetime.timedelta(hours=1), machine="pc", step="omo", status="failed", error="x"))
        s.commit()
        record(s, self._report("freshness: omo: nothing newer than 2026-11-02"), now=NOW)   # same fingerprint, names no step
        s.commit()
        assert s.query(DataIssue).one().first_seen == first

    def test_a_diagnosed_issue_with_no_current_streak_keeps_its_fingerprint_age(self):
        eng, s = _mem()
        first = NOW - datetime.timedelta(days=5)
        record(s, self._report(self._line()), now=first)
        s.add(StepRun(run_utc=NOW - datetime.timedelta(hours=1), machine="pc", step="omo", status="ok"))
        s.commit()
        record(s, self._report(self._line()), now=NOW)
        s.commit()
        assert s.query(DataIssue).one().first_seen == first

    def test_truncated_streak_says_at_least(self):
        """A streak older than the ledger's 30 days began at an unknown time: the edge must not be shown as the start."""
        eng, s = _mem()
        for d in range(0, SL.RETENTION_DAYS):
            s.add(StepRun(run_utc=NOW - datetime.timedelta(days=d, hours=1), machine="pc", step="omo", status="failed", error="x"))
        s.commit()
        record(s, self._report(self._line()), now=NOW)
        s.commit()
        row = s.query(DataIssue).one()
        assert "failing since at least" in row.message
        assert row.first_seen == NOW - datetime.timedelta(days=SL.RETENTION_DAYS - 1, hours=1)

    def test_fingerprint_and_dedupe_are_unchanged_by_the_diagnosis(self):
        """The same issue seen on two days is still ONE row, whether or not it carries a diagnosis or a since-note."""
        eng, s = _mem()
        s.add(StepRun(run_utc=NOW - datetime.timedelta(hours=1), machine="pc", step="omo", status="failed", error="x"))
        s.commit()
        record(s, self._report(self._line()), now=NOW - datetime.timedelta(days=1))
        record(s, self._report(self._line()), now=NOW)
        s.commit()
        row = s.query(DataIssue).one()
        assert row.occurrences == 2
