"""Days Bangladesh Bank does not publish — and the much larger set of days it
merely does not show you right now.

The gap check calls a missing working day inside a series our defect, and it is
right almost always: interbank_repo was missing 27-30 Sep because our fetcher
only ever read five trading days, OMO was missing ten days because BB renamed a
CSS class. Both ours.

Thursday 08-Oct-2026 is not. BB's interbank FX turnover table has no row for
it while call money, repo, OMO and reference rates all printed normally. No
fetch will fill that day, so as a defect it is one permanently red line — and
a check that is always red is a check nobody reads, which is how an eleven-day
outage hid in plain sight.

So an absence can be recorded and then skipped. The danger is obvious: the same
mechanism is a mute button for real gaps. These tests exist to keep it honest,
and the second one is the reason they exist at all — the first version of this
code recorded five absences that were not absences, because asking BB for the
CURRENT month returns a rolling tail of five trading days and says nothing
whatever about the days it leaves out.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DataAbsence, InterbankFx
from engines import absences

D = datetime.date
NO_HOLIDAYS: set = set()


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _working_days(lo: D, hi: D, skip=()) -> list:
    out, d = [], lo
    while d <= hi:
        if d.weekday() not in (4, 5) and d not in skip:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def _store(s, dates):
    for d in dates:
        s.add(InterbankFx(trade_date=d, segment="SPOT", num_deals=20,
                          volume_usd_mn=67.0, high_rate=123.5, low_rate=123.4,
                          war_rate=123.45))
    s.commit()


class TestAWholeMonthIsEvidence:
    def test_a_day_missing_from_a_complete_month_is_recorded(self):
        s = _mem()
        gap = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,))
        _store(s, fetched)
        got = absences.confirm_absences(s, "fxmarket", fetched, NO_HOLIDAYS,
                                        source="BB interbank FX page")
        s.commit()
        assert got == [gap], got
        row = s.query(DataAbsence).one()
        assert row.dataset == "fxmarket"
        assert "complete month" in (row.reason or "")
        assert row.source == "BB interbank FX page"

    def test_the_record_says_which_window_proved_it(self):
        s = _mem()
        gap = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,))
        _store(s, fetched)
        absences.confirm_absences(s, "fxmarket", fetched, NO_HOLIDAYS)
        s.commit()
        reason = s.query(DataAbsence).one().reason
        assert "2026-09-01" in reason and "2026-09-30" in reason, reason

    def test_re_confirming_counts_instead_of_duplicating(self):
        s = _mem()
        gap = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,))
        _store(s, fetched)
        for _ in range(3):
            absences.confirm_absences(s, "fxmarket", fetched, NO_HOLIDAYS)
            s.commit()
        assert s.query(DataAbsence).count() == 1
        assert s.query(DataAbsence).one().checks == 3


class TestARollingTailProvesNothing:
    """The mistake this code made on its first run, kept as a test.

    On 15-Oct-2026 BB's page returned 10-14 Oct for the current month and
    nothing earlier, although it HAD published 01-08 Oct and we hold those
    rows. Reading that window as evidence recorded five absences that were not
    absences — and would have excused any genuine gap in the same stretch.
    """

    def test_the_current_months_five_day_tail_records_nothing(self):
        s = _mem()
        tail = [D(2026, 10, d) for d in (10, 11, 12, 13, 14)]
        _store(s, tail)
        assert absences.confirm_absences(s, "fxmarket", tail, NO_HOLIDAYS) == []
        assert s.query(DataAbsence).count() == 0

    def test_a_partial_month_at_the_end_of_a_full_one_records_nothing_there(self):
        """September complete, October a tail. Only September can be judged."""
        s = _mem()
        sep_gap = D(2026, 9, 10)
        sep = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(sep_gap,))
        oct_tail = [D(2026, 10, d) for d in (10, 11, 12, 13, 14)]
        _store(s, sep + oct_tail)
        got = absences.confirm_absences(s, "fxmarket", sep + oct_tail, NO_HOLIDAYS)
        assert got == [sep_gap], got
        assert not any(d.month == 10 for d in got), "October proved nothing"

    def test_a_fetch_that_returned_almost_nothing_records_nothing(self):
        s = _mem()
        assert absences.confirm_absences(s, "fxmarket", [D(2026, 9, 1)], NO_HOLIDAYS) == []
        assert absences.confirm_absences(s, "fxmarket", [], NO_HOLIDAYS) == []


class TestADayWeAlreadyHaveIsNotAGap:
    def test_a_day_bb_dropped_but_we_stored_is_not_an_absence(self):
        """BB's archive shrinking is a fact about BB's website, not about what
        BB published — and we have the row to prove it published."""
        s = _mem()
        dropped = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(dropped,))
        _store(s, fetched + [dropped])          # we hold the day BB now omits
        assert absences.confirm_absences(s, "fxmarket", fetched, NO_HOLIDAYS) == []

    def test_a_gazetted_holiday_is_not_an_absence(self):
        s = _mem()
        hol = {D(2026, 9, 10)}
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=tuple(hol))
        _store(s, fetched)
        assert absences.confirm_absences(s, "fxmarket", fetched, hol) == []


class TestTheGapCheckRespectsOnlyWhatWasProved:
    def _gaps_for(self, s, dataset="fxmarket"):
        """The freshness gap list validate.py would produce, same rule."""
        import engines.expectations as EX
        present = {r[0] for r in s.execute(text(
            "SELECT DISTINCT trade_date FROM interbank_fx")).fetchall()}
        present = {d if hasattr(d, "year") else D.fromisoformat(str(d)[:10])
                   for d in present}
        gaps = EX.missing_working_days(present, min(present), max(present), NO_HOLIDAYS)
        return [g for g in gaps if g not in absences.absent_dates(s, dataset)]

    def test_an_unproved_gap_is_still_reported(self):
        s = _mem()
        gap = D(2026, 9, 10)
        _store(s, _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,)))
        assert self._gaps_for(s) == [gap]

    def test_a_proved_absence_is_passed_over(self):
        s = _mem()
        gap = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,))
        _store(s, fetched)
        absences.confirm_absences(s, "fxmarket", fetched, NO_HOLIDAYS)
        s.commit()
        assert self._gaps_for(s) == []

    def test_an_absence_for_one_dataset_does_not_excuse_another(self):
        s = _mem()
        gap = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,))
        _store(s, fetched)
        absences.confirm_absences(s, "repo", fetched, NO_HOLIDAYS)
        s.commit()
        assert self._gaps_for(s, "fxmarket") == [gap]

    def test_it_stays_visible_in_the_summary(self):
        """Skipped is not forgotten: the digest prints what is on record."""
        s = _mem()
        gap = D(2026, 9, 10)
        fetched = _working_days(D(2026, 9, 1), D(2026, 9, 30), skip=(gap,))
        _store(s, fetched)
        absences.confirm_absences(s, "fxmarket", fetched, NO_HOLIDAYS)
        s.commit()
        assert absences.summary(s) == {"fxmarket": {"count": 1, "latest": "2026-09-10"}}
