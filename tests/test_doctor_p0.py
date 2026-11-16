"""Doctor phase 0: the small fixes that stop the next 'repo stuck for five days' from being silent.

The repo incident (12-16 Nov 2026) had five chances to be caught. These tests close the cheap ones: a day the database
refuses must reach the run's errors, a runner's bare error must be attributed to its step, a BB block must not read as
'nothing published', the banner must not show a verdict the live data has already contradicted, and the sentinel must
not stay silent about a stale API or flood an issue with the same unchanged list.
"""
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest

import sentinel
import weekly_fetch as W
from fetchers import interbank_repo as IR


class TestADayTheDatabaseRefusedIsReported:
    def test_a_rejected_day_becomes_an_error_line_naming_the_step_and_the_days(self):
        msg = W.rejected_error("callmoney", {"rejected": ["2026-11-11"], "inserted": 3})
        assert msg.startswith("callmoney:") and "2026-11-11" in msg and "other days were stored" in msg

    def test_a_clean_batch_reports_nothing(self):
        assert W.rejected_error("callmoney", {"inserted": 3}) is None and W.rejected_error("x", None) is None

    def test_every_daily_writer_reports_its_rejected_days(self):
        """Only the repo step reported them; call money, reference rates, interbank FX and exchange rates dropped them into a
        log line. For exchange rates (the page keeps only today) a refused day is gone for good."""
        src = open(os.path.join(_ROOT, "weekly_fetch.py"), encoding="utf-8").read()
        for step in ("callmoney", "refrate", "interbank_fx", "fxrates", "interbank_repo"):
            assert f'rejected_error("{step}"' in src, f"{step} does not surface rejected days"


class TestARunnerErrorIsAttributedToItsStep:
    def test_a_bare_error_gets_its_step_prefix(self):
        assert W.prefixed("omo", ["OMO fetch failed: boom"]) == ["omo: OMO fetch failed: boom"]

    def test_an_already_prefixed_error_is_left_alone(self):
        assert W.prefixed("bgiib", ["bgiib: no figures"]) == ["bgiib: no figures"]

    def test_nothing_in_nothing_out(self):
        assert W.prefixed("treasury", None) == [] and W.prefixed("treasury", []) == []

    def test_the_freshness_rule_looks_for_exactly_this_prefix(self):
        """validate._fetch_is_healthy asks 'is there an error starting with <step>:' - a bare error read as a healthy fetch."""
        src = open(os.path.join(_ROOT, "validate.py"), encoding="utf-8").read()
        assert 'startswith(f"{step}:")' in src


class TestABlockedMonthIsAFailureNotNoData:
    def test_every_month_failing_raises_instead_of_returning_an_empty_list(self, monkeypatch):
        def boom(d):
            raise RuntimeError("BB answered with a block/challenge page")
        monkeypatch.setattr(IR, "fetch_interbank_repo_month", boom)
        with pytest.raises(RuntimeError):
            IR.fetch_interbank_repo_range(datetime.date(2026, 10, 1), datetime.date(2026, 11, 16))

    def test_one_month_failing_keeps_the_other(self, monkeypatch):
        def month(d):
            if d.month == 10:
                raise RuntimeError("blocked")
            return [{"trade_date": datetime.date(2026, 11, 3)}]
        monkeypatch.setattr(IR, "fetch_interbank_repo_month", month)
        got = IR.fetch_interbank_repo_range(datetime.date(2026, 10, 1), datetime.date(2026, 11, 16))
        assert [r["trade_date"] for r in got] == [datetime.date(2026, 11, 3)]

    def test_a_genuinely_empty_page_is_still_just_empty(self, monkeypatch):
        monkeypatch.setattr(IR, "fetch_interbank_repo_month", lambda d: [])
        assert IR.fetch_interbank_repo_range(datetime.date(2026, 10, 1), datetime.date(2026, 11, 16)) == []


class TestTheBannerDoesNotShowAVerdictTheLiveDataContradicts:
    def test_a_freshness_line_whose_dataset_is_live_current_is_dropped(self):
        """The escalation list is recorded by the last run (up to an hour old); `current` is computed live. Repo showed 'nothing
        newer than 9-Nov' after 10/11/12/15-Nov had been stored."""
        src = open(os.path.join(_ROOT, "frontend", "components", "DataWarning.tsx"), encoding="utf-8").read()
        assert "staleLine" in src and "datasets?.[key]?.current === true" in src
        assert "freshness:" in src


class TestTheSentinel:
    def _run(self, monkeypatch, api_commit, head_sha, head_age_min):
        def http(url, timeout=25):
            if "/api/meta/status" in url:
                ts = datetime.datetime.utcnow().isoformat()
                return 200, json.dumps({"last_run": ts, "last_run_errors": [], "data_health": {"ok": True},
                                        "datasets": {}})
            if "/health" in url:
                return 200, json.dumps({"status": "ok", "commit": api_commit})
            if "deploy-status" in url:
                return 200, json.dumps({"commitSha": "aaaaaaa"})
            return 200, "ok"
        monkeypatch.setattr(sentinel, "_http", http)

        def git(args, **kw):
            if "%ct" in " ".join(args):
                return str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - head_age_min * 60).encode()
            return head_sha.encode()
        monkeypatch.setattr(sentinel.subprocess, "check_output", git)
        return sentinel.check()

    def test_a_stale_api_deploy_is_a_problem(self, monkeypatch):
        v = self._run(monkeypatch, api_commit="1111111", head_sha="2222222", head_age_min=300)
        assert any("Stale API deploy" in p for p in v["problems"]), v["problems"]

    def test_a_current_api_deploy_is_not(self, monkeypatch):
        v = self._run(monkeypatch, api_commit="2222222", head_sha="2222222", head_age_min=300)
        assert not any("Stale API deploy" in p for p in v["problems"])

    def test_a_fresh_commit_is_given_its_grace_period_to_deploy(self, monkeypatch):
        v = self._run(monkeypatch, api_commit="1111111", head_sha="2222222", head_age_min=5)
        assert not any("Stale API deploy" in p for p in v["problems"])

    def test_the_uptime_issue_only_gets_a_comment_when_the_problems_change(self):
        """Issue #18 collected a comment every half hour for the same unchanged list."""
        y = open(os.path.join(_ROOT, ".github", "workflows", "sentinel.yml"), encoding="utf-8").read()
        assert "problems-sha" in y and "no new comment" in y

    def test_the_api_reports_the_commit_it_was_built_from(self):
        src = open(os.path.join(_ROOT, "api", "main.py"), encoding="utf-8").read()
        assert "VERCEL_GIT_COMMIT_SHA" in src and '"commit"' in src
