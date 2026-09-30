"""
Severity and the issue ledger.

Sep/Oct-2026: the integrity gate failed the build on ANY issue, so Bangladesh
Bank being late with two publications turned every refresh red — about 28 runs
a day for things nobody could act on. Meanwhile the daily digest, the one alarm
meant to be read, had died on its first run. A real failure would have been
indistinguishable from that wallpaper.

The fix is a distinction these tests pin down: stale data because BB has not
published (waiting — visible, aged, never red) versus stale data because OUR
fetch is broken (defect — always red). They are opposite problems that look
identical in the data, and the evidence that separates them is whether our own
fetch step ran without error.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate
from db import InterbankRepo, PipelineRun, PrimaryYieldSnapshot, DataIssue
from engines.guardian import record, record_repairs, open_issues, fingerprint

D = datetime.date
NOW = datetime.datetime.utcnow


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _stale_repo(s, days=9):
    """A repo row old enough to trip the >5 day freshness rule."""
    s.add(InterbankRepo(trade_date=D.today() - datetime.timedelta(days=days),
                        num_deals=30, amount_crore=2979.24, tenor_min_days=1,
                        tenor_max_days=7, rate_min_pct=8.5, rate_max_pct=8.95,
                        war_pct=8.8, ingested_utc=NOW()))


def _run(s, *, hours_ago=1.0, errors=None):
    s.add(PipelineRun(run_utc=NOW() - datetime.timedelta(hours=hours_ago), kind="refresh",
                      errors=json.dumps(errors) if errors else None, elapsed_sec=10))


def _check(monkeypatch, eng):
    monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
    return validate.integrity_check()


def _repo_lines(rep, key):
    return [x for x in rep[key] if x.startswith("freshness: repo")]


class TestSeverity:
    def test_bb_late_is_waiting_and_does_not_fail_the_build(self, monkeypatch):
        # Our fetch ran an hour ago and reported no error for this step, so the
        # staleness is BB's. It must be visible but must NOT turn the build red.
        eng, s = _mem(); _stale_repo(s); _run(s); s.commit()
        rep = _check(monkeypatch, eng)
        assert _repo_lines(rep, "waiting"), rep["waiting"]
        assert not _repo_lines(rep, "issues")
        assert rep["waiting_count"] >= 1

    def test_our_broken_fetch_is_a_defect_and_does_fail_the_build(self, monkeypatch):
        # Same stale data, but our own step errored — now it is ours to fix.
        eng, s = _mem(); _stale_repo(s)
        _run(s, errors=["interbank_repo: HTTPError 503"]); s.commit()
        rep = _check(monkeypatch, eng)
        assert _repo_lines(rep, "issues"), rep["issues"]
        assert not _repo_lines(rep, "waiting")
        assert rep["ok"] is False

    def test_the_same_staleness_reclassifies_when_the_fetch_breaks(self, monkeypatch):
        # The heart of the change: identical data, opposite verdicts, decided
        # only by whether OUR fetch is working.
        eng, s = _mem(); _stale_repo(s); _run(s); s.commit()
        assert _repo_lines(_check(monkeypatch, eng), "waiting")

        s.query(PipelineRun).delete()
        _run(s, errors=["interbank_repo: connection refused"]); s.commit()
        after = _check(monkeypatch, eng)
        assert _repo_lines(after, "issues") and not _repo_lines(after, "waiting")

    def test_a_dead_pipeline_makes_everything_our_defect(self, monkeypatch):
        # If the refresh has not run at all, nothing can be blamed on BB —
        # we have no evidence we even looked.
        eng, s = _mem(); _stale_repo(s); _run(s, hours_ago=100); s.commit()
        rep = _check(monkeypatch, eng)
        assert _repo_lines(rep, "issues") and not _repo_lines(rep, "waiting")

    def test_a_wrong_value_is_always_a_defect(self, monkeypatch):
        # Severity applies to FRESHNESS only. A bad number is ours no matter how
        # healthy the fetch is — BB's lateness can never excuse a wrong value.
        eng, s = _mem(); _run(s)
        s.add(PrimaryYieldSnapshot(snapshot_date=D.today(), auction_date=D.today(),
                                   issue_date=D.today() + datetime.timedelta(days=1),
                                   security_type="T_BOND", tenor_label="10Y", tenor_years=10,
                                   cutoff_yield_pct=99.0))
        s.commit()
        rep = _check(monkeypatch, eng)
        assert any("implausible cut-off" in i for i in rep["issues"]), rep["issues"]
        assert rep["ok"] is False


class TestLedger:
    def _report(self, waiting=(), issues=()):
        return {"issues": list(issues), "waiting": list(waiting),
                "by_table": {}, "by_table_waiting": {}}

    def test_the_same_problem_on_successive_days_is_one_row(self):
        # Dates change daily while the problem does not; digits are normalised
        # out of the fingerprint so the age keeps growing instead of resetting.
        eng, s = _mem()
        record(s, self._report(waiting=["freshness: repo: nothing newer than 2026-09-24"]))
        record(s, self._report(waiting=["freshness: repo: nothing newer than 2026-09-25"]))
        s.commit()
        rows = s.query(DataIssue).all()
        assert len(rows) == 1, [r.message for r in rows]
        assert rows[0].occurrences == 2 and rows[0].resolved_at is None

    def test_resolution_records_how_long_it_lived(self):
        eng, s = _mem()
        old = NOW() - datetime.timedelta(days=3)
        record(s, self._report(issues=["omo: 2026-09-01 something wrong"]), now=old)
        s.commit()
        record(s, self._report())                       # gone on the next run
        s.commit()
        row = s.query(DataIssue).one()
        assert row.resolved_at is not None
        assert "3d" in row.resolution, row.resolution

    def test_a_returning_problem_keeps_its_original_first_seen(self):
        # A recurring fault should read as recurring, not as brand new each time.
        eng, s = _mem()
        first = NOW() - datetime.timedelta(days=5)
        record(s, self._report(issues=["omo: 2026-09-01 flapping"]), now=first)
        record(s, self._report())
        r = record(s, self._report(issues=["omo: 2026-09-09 flapping"]))
        s.commit()
        row = s.query(DataIssue).one()
        assert r["reopened"] == 1
        assert row.resolved_at is None
        assert abs((row.first_seen - first).total_seconds()) < 2

    def test_severity_can_flip_on_an_existing_issue(self):
        # BB-late becoming our bug must update the row, not open a second one.
        eng, s = _mem()
        record(s, self._report(waiting=["freshness: repo: nothing newer than 2026-09-24"]))
        record(s, self._report(issues=["freshness: repo: nothing newer than 2026-09-24"]))
        s.commit()
        rows = s.query(DataIssue).all()
        assert len(rows) == 1 and rows[0].severity == "defect"

    def test_open_issues_puts_defects_first_then_oldest(self):
        eng, s = _mem()
        record(s, self._report(waiting=["freshness: repo: waiting"],
                               issues=["omo: real problem"]))
        s.commit()
        out = open_issues(s)
        assert [o["severity"] for o in out] == ["defect", "waiting"]

    def test_repairs_are_recorded_for_audit(self):
        eng, s = _mem()
        n = record_repairs(s, {"changes": ["yield_issue_date: 91D 2026-09-06 -> 2026-09-07",
                                           "coupon_payment_date: BD0001 moved"]})
        s.commit()
        assert n == 2
        from db import DataChange
        assert s.query(DataChange).count() == 2

    def test_fingerprint_ignores_digits_only(self):
        assert fingerprint("repo stale since 2026-09-24") == fingerprint("repo stale since 2026-10-01")
        assert fingerprint("repo stale") != fingerprint("fx stale")
