"""A closure BB announces by circular must reach the calendar - with proof - and move what it touches.

20-22 Oct 2026 was the Sharadiya Durga Puja closure, announced by SDAD circular letter 15 (11-Oct).
The gazetted list had 20 and 21; 22 looked like a missing working day, so every maturity that fell
on it stayed on a day the market was shut and the OMO ledger reported injections as missing that BB
had settled on 25-Oct. These tests keep the fix honest in both directions: a proven closure is
applied and everything derived from it moves; anything the code is not sure of is left PENDING and
loud, and the market overrules a circular.

The fixtures are the real text of BB's circulars (tests/fixtures/circulars): the PDF font garbles
the letters but not the digits, so the extractor has to cope with exactly this.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import calendar_utils
import db as dbmod
from db import CalendarNotice, CallMoneyRate, HolidayCalendar, OMOTransaction
from engines import calendar_notices as N

D = datetime.date
TODAY = D(2026, 10, 27)
FIX = os.path.join(_ROOT, "tests", "fixtures", "circulars")
PUJA_URL = "https://www.bb.org.bd/mediaroom/circulars/sdad/oct112026sdadl15.pdf"
PUJA_TITLE = "SDAD Circular Letter No. 15: Closure and Opening of Scheduled Banks on the Occasion of Sharadiya Durga Puja 2026"


def fixture(name):
    return open(os.path.join(FIX, name + ".txt"), encoding="utf-8").read()


@pytest.fixture(autouse=True)
def calendar():
    """The process-wide calendar is global state: load what BB's gazette said, put it back after."""
    saved = (calendar_utils._holiday_set, calendar_utils._working_weekend_set)
    calendar_utils.load_holidays({D(2026, 10, 20), D(2026, 10, 21)}, {D(2026, 10, 17)})
    yield
    calendar_utils._holiday_set, calendar_utils._working_weekend_set = saved


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _world():
    """The gazette's 20 and 21 Oct, a declared working Saturday, and a market that traded on the 19th and
    the 25th and was silent in between (BB's maintenance outage means nothing newer than the 25th yet)."""
    eng, s = _mem()
    for d, name, typ in ((D(2026, 10, 20), "Durga Puja", "GAZETTED"), (D(2026, 10, 21), "Durga Puja", "GAZETTED"),
                         (D(2026, 10, 17), "Declared working day", "WORKING_DAY")):
        s.add(HolidayCalendar(calendar_date=d, holiday_name=name, holiday_type=typ, fiscal_year="2026-27", source="seed"))
    for d in (D(2026, 10, 19), D(2026, 10, 25)):
        s.add(CallMoneyRate(trade_date=d, product="Overnight", maturity_days=1, amount_crore=100.0, average_rate_pct=9.2))
    s.commit()
    calendar_utils.load_calendar_rows(s.query(HolidayCalendar).all())
    return eng, s


class TestReadingTheCircularsDates:
    def test_the_puja_circular_gives_the_run_and_the_weekend_exactly(self):
        segs = N.extract_segments(fixture("puja"), TODAY)
        runs = [s for s in segs if s.kind == "range"]
        assert runs and runs[0].dates == [D(2026, 10, 20), D(2026, 10, 21), D(2026, 10, 22)]
        assert any(s.kind == "list" and s.dates == [D(2026, 10, 23), D(2026, 10, 24)] for s in segs)
        # 17-Oct is the Saturday the banks OPEN: it is a single date, never read as a closure
        assert any(s.kind == "single" and s.dates == [D(2026, 10, 17)] for s in segs)

    def test_a_year_that_is_not_near_is_not_a_date_of_this_notice(self):
        """The circular cites a 2019 circular it amends; that is a reference, not a closure."""
        assert not any(d.year == 2019 for s in N.extract_segments(fixture("puja"), TODAY) for d in s.dates)

    def test_a_month_word_it_has_never_seen_is_unreadable_not_guessed(self):
        segs = N.extract_segments(fixture("eid"), D(2026, 5, 18))
        assert segs and all(s.kind == "unreadable" for s in segs)
        assert "not one of the spellings" in segs[0].ambiguous

    def test_a_connector_it_does_not_know_between_day_numbers_is_ambiguous(self):
        """'20 xyz 22 October' could be a run or two separate days; half-reading it moves real dates."""
        segs = N.extract_segments("আগামী 20 কিছু 22 অক্টোবর 2026 বন্ধ", TODAY)
        assert segs and segs[0].kind == "unreadable" and "connector" in segs[0].ambiguous

    def test_a_run_is_never_half_read(self):
        segs = N.extract_segments("from 20 to 22 October 2026 the banks", TODAY)
        assert segs[0].kind == "range" and len(segs[0].dates) == 3


def _assess(segs, *, title=PUJA_TITLE, today=TODAY, silent=True, observed=D(2026, 10, 25), published=D(2026, 10, 11)):
    return N.assess(title, segs, today, is_workday=calendar_utils.is_working_day,
                    is_closed=lambda d: d in calendar_utils.get_loaded_holidays(),
                    activity=(lambda d: 0) if silent else (lambda d: 3), last_observed=observed, published=published)


class TestWhenACircularIsBelieved:
    def test_the_puja_closure_adds_only_the_day_the_gazette_missed(self):
        v = _assess(N.extract_segments(fixture("puja"), TODAY))
        assert v["status"] == "APPLIED" and v["dates"] == [D(2026, 10, 22)], v

    def test_the_market_overrules_the_circular(self):
        """If call money traded on a day the circular closes, the circular is not what happened."""
        v = _assess(N.extract_segments(fixture("puja"), TODAY), silent=False)
        assert v["status"] == "REJECTED" and "traded" in v["reason"]

    def test_silence_is_only_evidence_if_something_was_observed_afterwards(self):
        """BB's outage makes every recent day silent; that is the absence of observation, not a holiday."""
        v = _assess(N.extract_segments(fixture("puja"), TODAY), observed=D(2026, 10, 21))
        assert v["status"] == "PENDING" and "cannot be checked" in v["reason"]

    def test_a_future_day_not_next_to_a_known_closure_waits_for_a_person(self):
        segs = N.extract_segments("আগামী 3 ও 4 নভেম্বর 2026 বন্ধ", D(2026, 10, 27))
        v = _assess(segs, title="SDAD Circular Letter No. 99: Closure of Scheduled Banks")
        assert v["status"] == "PENDING" and "neither a declared general holiday" in v["reason"]

    def test_a_future_extension_of_a_gazetted_run_is_applied_before_the_day(self):
        calendar_utils.load_holidays({D(2026, 11, 3)}, set())
        segs = N.extract_segments("আগামী 3 ও 4 নভেম্বর 2026 বন্ধ", D(2026, 10, 27))
        v = _assess(segs, title="SDAD Circular Letter No. 99: Closure of Scheduled Banks")
        assert v["status"] == "APPLIED" and v["dates"] == [D(2026, 11, 4)]

    def test_a_government_declared_general_holiday_is_believed_for_a_single_future_day(self):
        segs = N.extract_segments("আগামী 5 নভেম্বর 2026 বন্ধ", D(2026, 10, 27))
        v = _assess(segs, title="SDAD Circular Letter No. 98: Declaration of General Holiday on the Occasion of X")
        assert v["status"] == "APPLIED" and v["dates"] == [D(2026, 11, 5)]

    def test_a_lone_reopening_date_is_not_mistaken_for_a_closure(self):
        """Eid circulars name the day the banks reopen; it is a normal working day."""
        segs = N.extract_segments("আগামী 2 নভেম্বর 2026 খোলা", D(2026, 10, 27))
        v = _assess(segs, title="SDAD Circular Letter No. 97: Scheduled Banks to Remain Open/Closed During Eid")
        assert v["status"] == "PENDING" and v["dates"] == []

    def test_the_general_holiday_on_a_saturday_changes_nothing(self):
        """7 Nov 2026 is a Saturday: already a weekend, so there is nothing to add."""
        v = _assess(N.extract_segments(fixture("nov7"), TODAY), published=D(2026, 10, 26),
                    title="SDAD Circular Letter No. 18: Declaration of General Holiday")
        assert v["status"] == "APPLIED" and v["dates"] == []

    def test_an_old_unreadable_circular_is_history_not_a_standing_defect(self):
        v = _assess(N.extract_segments(fixture("eid"), D(2026, 5, 18)), published=D(2026, 5, 18))
        assert v["status"] == "HISTORICAL"


class TestWritingTheCalendar:
    def _notice(self, s):
        n = CalendarNotice(url=PUJA_URL, published_date=D(2026, 10, 11), department="sdad", title=PUJA_TITLE, kind="CLOSURE")
        s.add(n)
        s.flush()
        return n

    def test_the_new_closure_carries_its_proof(self):
        eng, s = _world()
        N.apply_notice(s, self._notice(s), [D(2026, 10, 22)], "quote", D(2026, 10, 11))
        row = s.get(HolidayCalendar, D(2026, 10, 22))
        assert row.holiday_type == calendar_utils.CIRCULAR_CLOSURE_TYPE
        assert row.evidence_url == PUJA_URL and row.source == PUJA_URL and row.announced_date == D(2026, 10, 11)

    def test_a_declared_working_day_is_never_overwritten(self):
        eng, s = _world()
        out = N.apply_notice(s, self._notice(s), [D(2026, 10, 17)], "q", D(2026, 10, 11))
        assert s.get(HolidayCalendar, D(2026, 10, 17)).holiday_type == "WORKING_DAY" and out[0].startswith("!")

    def test_a_gazetted_day_keeps_its_type_and_gains_the_circular_as_evidence(self):
        """One writer per row: the gazette and the circular must not fight over a row's type."""
        eng, s = _world()
        n = self._notice(s)
        N._evidence_for_known_closures(s, n, [D(2026, 10, 20)], "quote")
        row = s.get(HolidayCalendar, D(2026, 10, 20))
        assert row.holiday_type == "GAZETTED" and row.evidence_url == PUJA_URL

    def test_the_new_type_closes_the_day_like_any_other_closure(self):
        calendar_utils.load_calendar_rows([(D(2026, 10, 22), calendar_utils.CIRCULAR_CLOSURE_TYPE)])
        assert not calendar_utils.is_working_day(D(2026, 10, 22))


class TestEverythingDerivedMovesInTheSameRun:
    def _row(self, **kw):
        return OMOTransaction(transaction_date=D(2026, 10, 13), maturity_date=D(2026, 10, 22), instrument="CB_REPO",
                              tenor_label="7D", tenor_days=7, accepted_bdt_crore=9978.0, maturity_bdt_crore=0.0,
                              rate_pct=9.5, direction="INJECTION", **kw)

    def test_a_tranche_that_matured_on_the_unknown_closure_is_rolled_to_the_25th(self):
        """The 25-Oct OMO ledger defect: BB printed 10,009 cr maturing on the 25th; we held it on the 22nd."""
        from engines.pipeline import propagate_calendar_change
        eng, s = _world()
        s.add(self._row())
        s.commit()
        rows = [{"url": PUJA_URL, "title": PUJA_TITLE, "published_date": D(2026, 10, 11), "department": "sdad"}]
        res = N.process(s, rows, lambda u: fixture("puja"), today=TODAY)
        s.commit()
        assert res["applied"] == 1 and res["changed_calendar"]
        assert s.get(HolidayCalendar, D(2026, 10, 22)).holiday_type == "CIRCULAR_CLOSURE"
        out = propagate_calendar_change(s)
        assert out["repaired"].get("omo-maturity-roll") == 1
        assert s.query(OMOTransaction).one().maturity_date == D(2026, 10, 25)

    def test_a_second_run_changes_nothing(self):
        from engines.pipeline import propagate_calendar_change
        eng, s = _world()
        s.add(self._row())
        s.commit()
        rows = [{"url": PUJA_URL, "title": PUJA_TITLE, "published_date": D(2026, 10, 11), "department": "sdad"}]
        N.process(s, rows, lambda u: fixture("puja"), today=TODAY); s.commit()
        propagate_calendar_change(s)
        again = N.process(s, rows, lambda u: fixture("puja"), today=TODAY)
        assert not again["changed_calendar"] and propagate_calendar_change(s)["repaired"] == {}


class TestNothingIsGuessed:
    def test_an_unreadable_circular_is_pending_and_writes_no_closure(self):
        eng, s = _world()
        rows = [{"url": "https://x/eid.pdf", "title": "SDAD Circular Letter No. 07: Remain Open/Closed During Eid",
                 "published_date": D(2026, 10, 20), "department": "sdad"}]
        res = N.process(s, rows, lambda u: fixture("eid"), today=TODAY)
        assert res["pending"] == 1
        assert s.query(HolidayCalendar).filter(HolidayCalendar.holiday_type == "CIRCULAR_CLOSURE").count() == 0

    def test_a_pdf_that_could_not_be_read_is_pending_and_loud(self):
        eng, s = _world()
        rows = [{"url": PUJA_URL, "title": PUJA_TITLE, "published_date": D(2026, 10, 11), "department": "sdad"}]
        res = N.process(s, rows, lambda u: None, today=TODAY)
        assert res["pending"] == 1 and res["errors"] and s.query(HolidayCalendar).count() == 3

    def test_a_page_that_was_not_served_changes_nothing(self):
        """Failing to read BB is never evidence about the calendar."""
        res = N.run_circular_watch(fetch_listing=lambda: {"rows": [], "transport_error": "BBMaintenance: maintenance page"})
        assert res["errors"] and "maintenance" in res["errors"][0] and res["applied"] == 0

    def test_a_human_confirm_without_a_quote_is_an_error_and_writes_nothing(self, tmp_path):
        f = tmp_path / "d.yaml"
        f.write_text("decisions:\n  - url: https://x/y.pdf\n    decision: confirm\n    dates: ['2026-10-22']\n", encoding="utf-8")
        good, errors = N.load_decisions(str(f))
        assert not good and errors and "quote" in errors[0]

    def test_a_human_confirm_settles_a_pending_circular(self, tmp_path):
        eng, s = _world()
        rows = [{"url": "https://x/eid.pdf", "title": "SDAD Circular Letter No. 07: Remain Open/Closed",
                 "published_date": D(2026, 10, 20), "department": "sdad"}]
        N.process(s, rows, lambda u: fixture("eid"), today=TODAY); s.commit()
        dec = [{"url": "https://x/eid.pdf", "decision": "confirm", "dates": [D(2026, 10, 22)], "quote": "from the PDF"}]
        res = N.process(s, rows, lambda u: fixture("eid"), today=TODAY, decisions=dec)
        assert res["applied"] == 1 and s.get(HolidayCalendar, D(2026, 10, 22)).holiday_type == "CIRCULAR_CLOSURE"

    def test_the_backlog_on_the_first_run_is_recorded_not_announced(self):
        from engines import announce
        eng, s = _world()
        rows = [{"url": PUJA_URL, "title": PUJA_TITLE, "published_date": D(2026, 10, 11), "department": "sdad"}]
        N.process(s, rows, lambda u: fixture("puja"), today=TODAY); s.commit()
        assert announce.pending(s, now=datetime.datetime(2026, 10, 27, 12)) == []


class TestTheFeed:
    LISTING = """<table>
      <tr><td>11/10/26</td><td>SDAD Circular Letter No. 15: Closure and Opening of Scheduled Banks on the Occasion of Sharadiya Durga Puja 2026</td><td><a href="/mediaroom/circulars/sdad/oct112026sdadl15.pdf">Download</a></td></tr>
      <tr><td>12/10/26</td><td>FCRPD Circular Letter No. 17: Closure and Opening of Finance Companies on the occasion of Sharadiya Durga Puja 2026</td><td><a href="/mediaroom/circulars/fid/oct122026fcrpdl17.pdf">Download</a></td></tr>
      <tr><td>25/06/26</td><td>SDAD Circular Letter No. 08: Keeping Bank Branches Operational on Saturdays During Mango Season</td><td><a href="/mediaroom/circulars/sdad/jun252026sdadl08.pdf">Download</a></td></tr>
      <tr><td>17/09/26</td><td>BRPD-1 Circular Letter No. 32: Pre-Finance Scheme to support the closed industry and service sectors.</td><td><a href="/mediaroom/circulars/brpd/sep172026brpd-1l32.pdf">Download</a></td></tr>
    </table>"""

    def test_only_the_scheduled_banks_closure_circulars_are_candidates(self):
        """Finance companies are not the money market; a few branches opening on Saturdays is not a bank
        holiday; a loan scheme for 'closed industries' is not about closing banks."""
        from fetchers import circulars as C
        rows = C.parse_listing(self.LISTING)
        assert [r["url"].rsplit("/", 1)[-1] for r in rows] == ["oct112026sdadl15.pdf"]
        assert rows[0]["published_date"] == D(2026, 10, 11)

    def test_a_pending_circular_is_a_defect_naming_it(self, monkeypatch):
        import validate
        eng, s = _world()
        s.add(CalendarNotice(url="https://x/eid.pdf", published_date=TODAY, title="Eid closure", kind="CLOSURE",
                             status="PENDING", reason="a date could not be read", first_seen_utc=datetime.datetime.utcnow()))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check(limit_per_rule=200)
        hits = [i for i in rep["issues"] if "PENDING" in i and "https://x/eid.pdf" in i]
        assert hits and "calendar_decisions.yaml" in hits[0]


class TestAGeneralHolidayNamesOnlyTheDayAfterTheLetter:
    def test_the_letters_own_date_and_the_memo_it_cites_are_not_closures(self):
        """Letter 18 of 26-Oct cites a government memo of 7-Oct and declares 7-Nov: only 7-Nov counts."""
        segs = N.extract_segments(fixture("nov7"), TODAY)
        v = N.assess("SDAD Circular Letter No. 18: Declaration of General Holiday", segs, TODAY,
                     is_workday=lambda d: True, is_closed=lambda d: False, activity=lambda d: 0,
                     last_observed=D(2026, 10, 25), published=D(2026, 10, 26))
        assert v["status"] == "APPLIED" and v["dates"] == [D(2026, 11, 7)]
