"""The refresh must keep running even when GitHub's scheduler does not.

On 25-Oct-2026 the "hourly" refresh was delivered six times in 22 hours, the
last during a BB outage, and BB's return went unnoticed for hours. The watchdog
starts a refresh itself when one is due. These tests pin the two ways such a
thing goes wrong: it stays silent when it should act, or it floods when it
should wait.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog as W

NOW = datetime.datetime(2026, 10, 25, 20, 0, 0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestWhenToStartARefresh:
    def test_bb_answers_and_the_last_run_is_old_so_it_starts(self):
        assert W.decide(75, "up", 0, 90)["dispatch"]

    def test_a_recent_run_is_left_alone(self):
        assert not W.decide(20, "up", 0, 60)["dispatch"]

    def test_never_on_top_of_a_run_in_flight(self):
        """Two refreshes at once is the flood; the first is still working."""
        assert not W.decide(500, "up", 1, 90)["dispatch"]

    def test_never_twice_in_the_cooldown(self):
        """A just-queued run has not yet written a record, so the age still looks
        old; without a cooldown the next tick would start another."""
        assert not W.decide(500, "up", 0, 5)["dispatch"]

    def test_bb_down_is_looked_at_rarely_not_never(self):
        """Never would hide the day BB returns; every ten minutes would hammer a
        site that has said no."""
        assert not W.decide(75, "maintenance", 0, 90)["dispatch"]
        assert W.decide(200, "maintenance", 0, 90)["dispatch"]
        assert W.decide(200, "unreachable", 0, 90)["dispatch"]

    def test_bb_coming_back_is_picked_up_at_once_if_the_last_run_is_old(self):
        assert W.decide(200, "up", 0, 90)["dispatch"]

    def test_partial_counts_as_answering(self):
        assert W.decide(75, "partial", 0, 90)["dispatch"]

    def test_unknown_age_refreshes_only_if_bb_answers(self):
        assert W.decide(None, "up", 0, 90)["dispatch"]
        assert not W.decide(None, "unknown", 0, 90)["dispatch"]

    def test_a_reason_is_always_given(self):
        assert all(W.decide(a, s, r, c)["why"]
                   for a in (None, 10, 500) for s in ("up", "maintenance")
                   for r in (0, 1) for c in (None, 5, 90))


class TestReadingTheQueue:
    def test_queued_and_in_progress_count_as_running(self):
        runs = [{"status": "completed", "createdAt": "2026-10-25T18:00:00Z"},
                {"status": "queued", "createdAt": "2026-10-25T19:55:00Z"},
                {"status": "in_progress", "createdAt": "2026-10-25T19:00:00Z"}]
        running, since = W._summarise_runs(runs, NOW)
        assert running == 2 and round(since) == 5

    def test_no_runs_at_all(self):
        assert W._summarise_runs([], NOW) == (0, None)

    def test_an_unreadable_queue_dispatches_nothing_blind(self, monkeypatch):
        """If the queue cannot be seen, starting a run could double up."""
        import subprocess as sp

        def _bad(*a, **k):
            class R:
                returncode, stdout, stderr = 1, "", "HTTP 502"
            return R()
        monkeypatch.setattr(W, "_gh_runs", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        called = []
        monkeypatch.setattr(sp, "run", lambda *a, **k: called.append(a))
        import fetchers.bb_health as H
        monkeypatch.setattr(H, "probe", lambda **k: {"state": "up"})
        assert W.main() == 1 and called == []


class TestTheWorkflows:
    def _read(self, name):
        with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_watchdog_runs_often_and_may_start_workflows(self):
        wf = self._read("refresh_watchdog.yml")
        assert '*/10 * * * *' in wf
        assert "actions: write" in wf and "python watchdog.py" in wf

    def test_a_new_trigger_cannot_kill_a_refresh_in_progress(self):
        """With cancel-in-progress true, more frequent triggers mean runs that
        are killed before they finish - the opposite of the point."""
        wf = self._read("hourly_refresh.yml")
        assert "cancel-in-progress: false" in wf and "cancel-in-progress: true" not in wf

    def test_the_workflow_it_starts_exists_and_can_be_dispatched(self):
        wf = self._read(W.WORKFLOW)
        assert "workflow_dispatch" in wf
