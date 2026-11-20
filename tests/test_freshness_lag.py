"""A daily series is 'current' against BB's real publishing cadence, not against a wish.

Measured from first-ingest history: BB posts the interbank FX, exchange-rate and repo pages one working day AFTER the day they describe
(Thu 12-Nov was first stored Sun 15-Nov). Judging them against 'yesterday' opened a fresh uptime alert (#61) every Friday and every morning.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

import freshness_logic as F

FRI, SAT, SUN, MON, WED, THU = (datetime.date(2026, 11, d) for d in (20, 21, 22, 23, 25, 26))


def test_on_a_friday_the_thursday_fx_figure_is_not_yet_due():
    """Thursday's FX is posted on Sunday, so holding Wednesday's on Friday is current - the false alarm this prevents."""
    for key in ("fxmarket", "fxrates", "repo"):
        assert F.required_date(key, FRI) == datetime.date(2026, 11, 18)
        assert F.required_date(key, SAT) == datetime.date(2026, 11, 18)


def test_a_same_day_series_still_needs_thursdays_figure_on_friday():
    """Call money is posted the day itself: not holding Thursday's on Friday is a real gap and must still be flagged."""
    assert F.required_date("callmoney", FRI) == datetime.date(2026, 11, 19)


def test_the_lag_is_exactly_one_working_day_not_a_free_pass():
    """Each working day pushes the requirement forward by one: still holding Wednesday's on Monday is behind."""
    assert F.required_date("repo", SUN) == datetime.date(2026, 11, 18)     # Sun: Wed (Thu's posts today, read in the evening)
    assert F.required_date("repo", MON) == datetime.date(2026, 11, 19)     # Mon: Thu is now required
    assert F.required_date("repo", WED) == datetime.date(2026, 11, 23)     # Wed: Monday's


def test_the_lagged_set_matches_the_dataset_declared_as_previous_day_in_expectations():
    from engines import expectations
    fx = next(d for d in expectations.DATASETS if d.key == "fxrates")
    assert "PREVIOUS business day" in fx.note and "fxrates" in F.PUBLISHED_NEXT_WORKING_DAY
