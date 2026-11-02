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
