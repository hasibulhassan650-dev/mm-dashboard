"""Two refreshes on one machine must not both drive Chrome: the second steps aside, and a crashed run's lock expires."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import weekly_fetch as W


def test_a_second_refresh_does_not_start_while_the_first_holds_the_lock(tmp_path):
    p = str(tmp_path / "r.lock")
    assert W.acquire_lock(p) == p
    assert W.acquire_lock(p) is None


def test_a_released_lock_can_be_taken_again(tmp_path):
    p = str(tmp_path / "r.lock")
    W.acquire_lock(p)
    os.remove(p)
    assert W.acquire_lock(p) == p


def test_a_crashed_runs_lock_expires_so_refreshes_cannot_stop_for_ever(tmp_path):
    p = str(tmp_path / "r.lock")
    W.acquire_lock(p)
    later = os.path.getmtime(p) + W.LOCK_STALE_SECONDS + 60
    assert W.acquire_lock(p, now=later) == p


def test_a_killed_runs_lock_does_not_block_the_next_refresh(tmp_path):
    """The hourly refresh was killed two minutes in (its task was re-registered) and left its lock; every run for the next
    three hours then 'stepped aside' for a run that no longer existed. A lock whose holder is gone is not a lock."""
    p = str(tmp_path / "r.lock")
    open(p, "w").write("99999999")                       # a process id that is not running
    assert W.acquire_lock(p) == p


def test_a_lock_held_by_a_live_process_still_blocks(tmp_path):
    p = str(tmp_path / "r.lock")
    assert W.acquire_lock(p) == p                        # taken by THIS (live) process
    assert W.acquire_lock(p) is None
    assert W._pid_alive(os.getpid()) and not W._pid_alive(99999999)
