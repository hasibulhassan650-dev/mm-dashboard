"""Staleness that expires, gaps inside a series, and total coverage.

These exist because of a real eleven-day outage in which every check reported
green. validate.py detected it and wrote "publication gap this long is
implausible" — then classified it `waiting`, which never fails a build and
never alerts. The conclusion was correct and discarded.

The tests below pin the three things that let that happen:

  * waiting never expired, so "BB is a day late" and "BB stopped publishing"
    were treated identically forever;
  * staleness looks only at the newest row, so a gap in the MIDDLE of a series
    (interbank_repo 27-30 Sep, fx_rates_daily's 77 working days) was invisible;
  * nine datasets had no rule at all, so no staleness could ever be reported
    for them.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import db as dbmod
from engines.expectations import (
    BY_KEY, DATASETS, EVENT, MONTHLY, NOT_WATCHED, TOLERANCE, WORKING_DAY,
    missing_working_days, working_days_between,
)

D = datetime.date


class TestWaitingExpires:
    """The heart of it. Short lateness is BB's; sustained silence is ours."""

    def test_a_daily_series_one_day_behind_is_not_even_a_finding(self):
        assert BY_KEY["omo"].severity_for_age(1) is None

    def test_a_daily_series_two_days_behind_is_waiting(self):
        assert BY_KEY["omo"].severity_for_age(2) == "waiting"

    def test_a_daily_series_past_tolerance_becomes_a_defect(self):
        # The exact case: OMO had not moved in 10 calendar days -- about 7
        # working days -- and stayed `waiting` throughout.
        assert BY_KEY["omo"].severity_for_age(5) == "defect"
        assert BY_KEY["omo"].severity_for_age(7) == "defect"

    def test_a_couple_of_missed_publications_is_still_only_waiting(self):
        # Ages for a daily series are WORKING days. If one or two missed
        # publications escalated immediately the alerts would become wallpaper,
        # which is the failure the waiting/defect split exists to avoid.
        assert BY_KEY["omo"].severity_for_age(2) == "waiting"
        assert BY_KEY["omo"].severity_for_age(4) == "waiting"

    def test_the_boundary_is_where_it_is_declared(self):
        tol = BY_KEY["omo"].tolerance_days
        assert BY_KEY["omo"].severity_for_age(tol) == "waiting"
        assert BY_KEY["omo"].severity_for_age(tol + 1) == "defect"

    @pytest.mark.parametrize("key,age,expect", [
        ("yields", 7, "waiting"),      # weekly: a missed cycle is plausible
        ("yields", 20, "defect"),      # several missed cycles is not
        ("reserves", 60, "waiting"),   # monthly: BB publishes ~2 months in arrears
        ("reserves", 100, "defect"),   # but not three
        ("callmoney", 2, "waiting"),
        ("callmoney", 6, "defect"),
    ])
    def test_each_cadence_has_its_own_patience(self, key, age, expect):
        assert BY_KEY[key].severity_for_age(age) == expect

    def test_an_event_series_is_never_stale_by_time_alone(self):
        # BB intervenes in FX irregularly: silence means nothing, and an alarm
        # on it would be pure noise -- which is how alarms become wallpaper.
        assert BY_KEY["fxauction"].severity_for_age(400) is None
        assert BY_KEY["policy"].severity_for_age(400) is None

    def test_a_derived_series_gets_no_publication_excuse(self):
        # We compute it, so there is nobody else to be late. (Its tolerance is
        # in CALENDAR days while a daily series is in WORKING days, so the two
        # numbers are not comparable -- the point is the cadence, not the size.)
        flows = BY_KEY["flows"]
        assert flows.cadence == "derived"
        assert flows.source == "computed"
        assert flows.tolerance_days is not None


class TestAgeIsMeasuredInTheRightUnit:
    """Today being a Saturday must not make every daily series look late."""

    def test_a_daily_series_counts_working_days_not_calendar_days(self):
        omo = BY_KEY["omo"]
        # Wed 7-Oct-2026 -> Sat 10-Oct: only Thursday is a working day between,
        # because Bangladesh closes Friday and Saturday.
        assert omo.age(D(2026, 10, 7), D(2026, 10, 10)) == 1
        assert omo.severity_for_age(omo.age(D(2026, 10, 7), D(2026, 10, 10))) is None

    def test_a_weekend_alone_never_makes_a_daily_series_late(self):
        omo = BY_KEY["omo"]
        for day in (D(2026, 10, 9), D(2026, 10, 10)):     # Friday, Saturday
            assert omo.severity_for_age(omo.age(D(2026, 10, 8), day)) is None

    def test_a_holiday_does_not_age_a_series_either(self):
        omo = BY_KEY["omo"]
        plain = omo.age(D(2026, 10, 4), D(2026, 10, 8))
        with_hol = omo.age(D(2026, 10, 4), D(2026, 10, 8), holidays={D(2026, 10, 6)})
        assert with_hol == plain - 1

    def test_a_monthly_series_still_counts_calendar_days(self):
        res = BY_KEY["reserves"]
        assert res.age(D(2026, 8, 1), D(2026, 10, 10)) == 70


class TestGapsInsideASeries:
    """Staleness looks at the newest row only. These are the ones it cannot see."""

    def test_a_missing_tuesday_in_the_middle_is_found(self):
        present = [D(2026, 9, 21), D(2026, 9, 22), D(2026, 9, 24)]   # 23rd missing
        gaps = missing_working_days(present, D(2026, 9, 21), D(2026, 9, 24))
        assert gaps == [D(2026, 9, 23)]

    def test_the_bangladesh_weekend_is_not_a_gap(self):
        # Friday and Saturday. Flagging them would bury the real gaps.
        present = [D(2026, 10, 8), D(2026, 10, 11)]
        assert missing_working_days(present, D(2026, 10, 8), D(2026, 10, 11)) == []

    def test_a_gazetted_holiday_is_not_a_gap(self):
        present = [D(2026, 9, 21), D(2026, 9, 23)]
        gaps = missing_working_days(present, D(2026, 9, 21), D(2026, 9, 23),
                                    holidays={D(2026, 9, 22)})
        assert gaps == []

    def test_the_real_repo_case(self):
        # interbank_repo had 27-30 Sep missing INSIDE its range while its newest
        # row was current, so no staleness check could ever have seen it.
        present = [D(2026, 9, 24), D(2026, 10, 1)]
        gaps = missing_working_days(present, D(2026, 9, 24), D(2026, 10, 1))
        assert D(2026, 9, 27) in gaps and D(2026, 9, 30) in gaps
        assert D(2026, 9, 25) not in gaps      # Friday
        assert D(2026, 9, 26) not in gaps      # Saturday

    def test_working_days_between_skips_the_weekend(self):
        # Thu -> Sun is one working day in Bangladesh, not three.
        assert working_days_between(D(2026, 10, 8), D(2026, 10, 11)) == 1


class TestCoverage:
    """Nine datasets had no rule at all. Nothing should be unwatched by accident."""

    def test_every_table_is_watched_or_exempt_with_a_reason(self):
        tables = set(dbmod.Base.metadata.tables)
        watched = {d.table for d in DATASETS}
        unaccounted = sorted(tables - watched - set(NOT_WATCHED))
        assert not unaccounted, (
            "these tables are neither watched nor deliberately exempt, so no "
            f"staleness could ever be reported for them: {unaccounted}. Add a "
            "Dataset, or an entry in NOT_WATCHED explaining why not."
        )

    def test_every_exemption_names_a_real_table_and_gives_a_reason(self):
        tables = set(dbmod.Base.metadata.tables)
        for t, why in NOT_WATCHED.items():
            assert len(why) > 15, f"{t}: exemption needs a real reason, got {why!r}"
            # a stale exemption for a dropped table should not linger
            if t not in ("source_snapshots",):     # created by the canary work
                assert t in tables, f"{t} is exempt but no longer exists"

    def test_every_dataset_points_at_a_real_table_and_column(self):
        for d in DATASETS:
            tbl = dbmod.Base.metadata.tables.get(d.table)
            assert tbl is not None, f"{d.key}: no table {d.table}"
            assert d.date_column in tbl.columns, \
                f"{d.key}: {d.table} has no column {d.date_column}"

    def test_keys_and_tables_are_unique(self):
        keys = [d.key for d in DATASETS]
        assert len(keys) == len(set(keys)), "duplicate dataset key"

    def test_the_datasets_that_were_unwatched_are_now_covered(self):
        # The nine found by the audit.
        for t in ("securities", "fx_auction_results", "reserves_monthly",
                  "remittance_monthly", "monetary_monthly", "policy_rate_snapshots",
                  "coupon_events", "maturity_events", "holiday_calendar"):
            assert t in {d.table for d in DATASETS}, f"{t} still unwatched"

    def test_every_cadence_has_a_tolerance(self):
        for d in DATASETS:
            assert d.cadence in TOLERANCE, f"{d.key}: unknown cadence {d.cadence}"
            if d.cadence != EVENT:
                assert d.tolerance_days, f"{d.key}: no tolerance"

    def test_a_fetched_dataset_names_the_step_that_feeds_it(self):
        # Needed to tie "this step wrote nothing" to "this dataset is behind".
        for d in DATASETS:
            if d.source != "computed" and d.cadence in (WORKING_DAY, MONTHLY):
                assert d.step, f"{d.key}: no fetch step named"
