"""BB being DOWN is neither "BB has not published" nor "our fetch is broken".

From 22-Oct-2026 BB's website served a maintenance page. The guardian had two
explanations for a stale feed and used the wrong one: every BB-fed series was
reported as "OUR fetch is not current" and, past its tolerance, as a fault in
our own code ("a gap beyond N days is ours, not BB's"). That sends the desk to
debug the wrong system while the real action - check with BB, tell the desk the
numbers are frozen - goes undone.

The properties these tests hold, in order of how easily they are broken:
  1. during an outage the blame moves to BB, and the severity does NOT drop;
  2. the moment a probe says BB is up, it flips straight back to "ours";
  3. a stale probe never excuses today's staleness;
  4. a day is only "silent" if something was observed AFTER it, or an outage
     invites exactly the false closure that rolls real settlements.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate
from db import CallMoneyRate, InterbankRepo, PipelineRun
from engines import bb_outage as O

D = datetime.date
NOW = datetime.datetime.utcnow


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _working_days_ago(n):
    import engines.expectations as EX
    d = D.today()
    while EX.working_days_between(d, D.today(), set()) < n:
        d -= datetime.timedelta(days=1)
    return d


def _repo(s, age):
    s.add(InterbankRepo(trade_date=_working_days_ago(age), num_deals=30, amount_crore=2979.0,
                        tenor_min_days=1, tenor_max_days=7, rate_min_pct=8.5,
                        rate_max_pct=8.95, war_pct=8.8, ingested_utc=NOW()))


def _run(s, state, hours_ago=1.0, errors=None):
    q = {"ok": True, "issue_count": 0, "ledger": {"opened": 0}}
    if state:
        probed = (NOW() - datetime.timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        q["bb_status"] = {"state": state, "detail": [], "probed_utc": probed}
    s.add(PipelineRun(run_utc=NOW() - datetime.timedelta(hours=hours_ago), kind="refresh",
                      errors=json.dumps(errors) if errors else None, quality=json.dumps(q)))


def _verdict(monkeypatch, eng):
    monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
    # A fresh process has made no probe of its own; only the recorded one counts.
    from fetchers import bb_health
    monkeypatch.setattr(bb_health, "LAST_PROBE", None)
    return validate.integrity_check(limit_per_rule=500)


def _repo_lines(rep, key):
    return [x for x in rep[key] if x.startswith("freshness: repo")]


class TestTheBlameMovesButTheSeverityDoesNot:
    def test_a_stale_feed_during_an_outage_names_bb(self, monkeypatch):
        eng, s = _mem()
        _repo(s, age=3)
        _run(s, "maintenance")
        s.commit()
        rep = _verdict(monkeypatch, eng)
        line = _repo_lines(rep, "waiting")
        assert line, rep["waiting"]
        assert "under maintenance" in line[0]
        assert "OUR fetch" not in line[0]

    def test_past_its_tolerance_it_still_escalates_but_does_not_blame_us(self, monkeypatch):
        """The desk reading numbers eight working days old needs to hear it
        whoever's fault it is. Only the owner and the action change."""
        eng, s = _mem()
        _repo(s, age=8)
        _run(s, "maintenance")
        s.commit()
        rep = _verdict(monkeypatch, eng)
        line = _repo_lines(rep, "issues")
        assert line, "an eight-day-old feed stopped being a defect because BB is down"
        assert "ESCALATED" in line[0] and "under maintenance" in line[0]
        assert "is ours, not BB" not in line[0], "the guardian blamed our own code"
        assert "Not a fault in our fetch" in line[0]

    def test_there_is_one_plain_line_saying_so(self, monkeypatch):
        eng, s = _mem()
        _repo(s, age=3)
        _run(s, "maintenance")
        s.commit()
        rep = _verdict(monkeypatch, eng)
        top = [x for x in rep["waiting"] if x.startswith("bb-outage")]
        assert top and "Nothing to fix on our side" in top[0]

    def test_a_partial_outage_counts_too(self, monkeypatch):
        """On 24-Oct plain-HTTP pages were served (stale) while the F5-protected
        ones showed the maintenance page. Half-down is still frozen."""
        eng, s = _mem()
        _repo(s, age=3)
        _run(s, "partial")
        s.commit()
        assert "under maintenance" in _repo_lines(_verdict(monkeypatch, eng), "waiting")[0]


class TestItFlipsBackTheMomentBBIsUp:
    def test_with_bb_up_a_stale_feed_is_ours_again(self, monkeypatch):
        eng, s = _mem()
        _repo(s, age=8)
        _run(s, "up")
        s.commit()
        rep = _verdict(monkeypatch, eng)
        line = _repo_lines(rep, "issues")
        assert line and "ours, not BB" in line[0], \
            "BB is up and the feed is still stale - that is our fetch, and it must say so"
        assert "under maintenance" not in line[0]

    def test_with_no_probe_recorded_nothing_is_excused(self, monkeypatch):
        """Runs that predate the probe say nothing about BB, and silence must
        never be read as 'BB was down'."""
        eng, s = _mem()
        _repo(s, age=8)
        _run(s, None)
        s.commit()
        line = _repo_lines(_verdict(monkeypatch, eng), "issues")
        assert line and "under maintenance" not in line[0]

    def test_an_unknown_probe_does_not_excuse_either(self, monkeypatch):
        """Could not tell is not 'down'. Calling it down would blame BB for our
        own network; calling it up would hide an outage - so it excuses nothing."""
        eng, s = _mem()
        _repo(s, age=8)
        _run(s, "unknown")
        s.commit()
        assert "under maintenance" not in _repo_lines(_verdict(monkeypatch, eng), "issues")[0]

    def test_a_stale_probe_never_excuses_todays_staleness(self, monkeypatch):
        """'BB was down two days ago' is not evidence about now."""
        eng, s = _mem()
        _repo(s, age=8)
        _run(s, "maintenance", hours_ago=60)
        s.commit()
        rep = _verdict(monkeypatch, eng)
        assert not [x for x in rep["issues"] + rep["waiting"] if "under maintenance" in x]


class TestOnlyBBSourcedFeedsAreExcused:
    def test_our_own_projection_is_never_blamed_on_bb(self, monkeypatch):
        """flows_forward is built by us. If it is short, that is ours whatever
        BB's website is doing."""
        eng, s = _mem()
        _run(s, "maintenance")
        s.commit()
        rep = _verdict(monkeypatch, eng)
        line = [x for x in rep["issues"] + rep["waiting"]
                if x.startswith("freshness: flows_forward")]
        assert line and "under maintenance" not in line[0]


class TestAnOutageDayIsNotAClosure:
    """BB's outage turned every later day into "whole money market silent, not
    in holiday_calendar: seed it as a closure" - an invitation to insert a false
    holiday, which rolls real coupons and settlements onto the wrong days."""

    def _call(self, s, d):
        s.add(CallMoneyRate(trade_date=d, product="CALL", maturity_days=1, amount_crore=5000.0,
                            highest_rate_pct=10.0, lowest_rate_pct=9.0, average_rate_pct=9.5,
                            num_deals=40))

    def _silent_days(self, rep):
        out = set()
        for x in rep["issues"]:
            m = re.search(r"(\d{4}-\d{2}-\d{2}) \(\w{3}\) . whole money market silent", x)
            if x.startswith("calendar:") and m:
                out.add(D.fromisoformat(m.group(1)))
        return out

    def _world(self):
        eng, s = _mem()
        d, mid = D(2026, 9, 1), D(2026, 9, 15)
        while d <= D(2026, 9, 30):
            if d.weekday() in (0, 1, 2, 3, 6) and d != mid:
                self._call(s, d)
            d += datetime.timedelta(days=1)
        _run(s, "maintenance")
        s.commit()
        return eng, mid

    def test_days_after_the_newest_print_are_not_silence(self, monkeypatch):
        eng, _ = self._world()
        after = [d for d in self._silent_days(_verdict(monkeypatch, eng)) if d > D(2026, 9, 30)]
        assert after == [], f"outage days were inferred to be closures: {sorted(after)[:4]}"

    def test_a_silent_day_in_the_middle_is_still_caught(self, monkeypatch):
        """The rule must narrow the inference, not switch it off: a real
        unseeded closure between two active days is exactly what it is for."""
        eng, mid = self._world()
        assert mid in self._silent_days(_verdict(monkeypatch, eng))


class TestReadingTheRunHistory:
    @staticmethod
    def _q(state, minutes_ago):
        t = (NOW() - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        return json.dumps({"bb_status": {"state": state, "probed_utc": t, "detail": []}})

    def test_since_is_the_earliest_probe_of_the_unbroken_stretch(self):
        runs = [(NOW() - datetime.timedelta(minutes=10), self._q("maintenance", 10)),
                (NOW() - datetime.timedelta(minutes=70), self._q("maintenance", 70)),
                (NOW() - datetime.timedelta(minutes=130), self._q("maintenance", 130))]
        out = O.outage_from_runs(runs)
        assert out["runs"] == 3
        assert out["since"] == json.loads(runs[2][1])["bb_status"]["probed_utc"]

    def test_an_up_run_ends_the_stretch(self):
        runs = [(NOW() - datetime.timedelta(minutes=10), self._q("maintenance", 10)),
                (NOW() - datetime.timedelta(minutes=70), self._q("up", 70)),
                (NOW() - datetime.timedelta(minutes=130), self._q("maintenance", 130))]
        assert O.outage_from_runs(runs)["runs"] == 1, "a recovery in the middle was ignored"

    def test_a_run_with_no_probe_ends_it_rather_than_being_guessed_down(self):
        """Inventing a start date for runs nobody probed would manufacture a
        duration nobody observed."""
        runs = [(NOW() - datetime.timedelta(minutes=10), self._q("maintenance", 10)),
                (NOW() - datetime.timedelta(minutes=70), json.dumps({"ok": True}))]
        assert O.outage_from_runs(runs)["runs"] == 1

    def test_the_newest_run_being_up_means_no_outage(self):
        runs = [(NOW() - datetime.timedelta(minutes=10), self._q("up", 10)),
                (NOW() - datetime.timedelta(minutes=70), self._q("maintenance", 70))]
        assert O.outage_from_runs(runs) is None

    def test_no_runs_means_no_outage(self):
        assert O.outage_from_runs([]) is None

    def test_this_processs_own_probe_is_seen_before_the_run_is_recorded(self, monkeypatch):
        """weekly_fetch runs the integrity check BEFORE it writes its run row. A
        verdict that could not see its own probe would tell the desk BB was up
        in the very run that found it down."""
        from fetchers import bb_health
        eng, s = _mem()
        _run(s, "up")
        s.commit()
        monkeypatch.setattr(bb_health, "LAST_PROBE",
                            {"state": "maintenance",
                             "probed_utc": NOW().isoformat(timespec="seconds"), "detail": []})
        out = O.current(s)
        assert out and out["state"] == "maintenance"


def test_weekly_fetch_records_the_probe_with_the_run():
    """The record is what every later check reads. A probe that ran but was not
    stored would be invisible to the digest, the site and the next run."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "weekly_fetch.py"), encoding="utf-8").read()
    assert "_bb_probe()" in src and 'quality["bb_status"] = bb_status' in src
    assert src.index("_bb_probe()") < src.index("# ── 0. Self-heal"), \
        "the probe must run before any BB-dependent step"


class TestTheSiteAgreesWithTheGuardian:
    """The API cannot import engines/ (deploy boundary), so meta._bb_outage
    re-implements the rule that engines/bb_outage.py applies. Two copies of one
    rule drift - so this holds them to the same answers."""

    def _meta(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.append(os.path.join(root, "api", "routers"))
        import meta
        return meta

    def test_maintenance_and_partial_are_reported(self):
        m = self._meta()
        for state in ("maintenance", "partial"):
            out = m._bb_outage({"bb_status": {"state": state, "probed_utc": "2026-10-24T19:15:00"}}, 1.0)
            assert out == {"state": state, "probed_utc": "2026-10-24T19:15:00"}

    def test_up_unknown_and_missing_are_not_an_outage(self):
        m = self._meta()
        for st in ({"state": "up"}, {"state": "unknown"}, None):
            assert m._bb_outage({"bb_status": st} if st else {}, 1.0) is None
        assert m._bb_outage(None, 1.0) is None

    def test_a_probe_older_than_36_hours_excuses_nothing_here_either(self):
        m = self._meta()
        st = {"bb_status": {"state": "maintenance"}}
        assert m._bb_outage(st, 36.0) is None
        assert m._bb_outage(st, 40.0) is None
        assert m._bb_outage(st, float("inf")) is None, "no run at all must not look like an outage"

    def test_the_36_hour_window_is_the_same_number_in_both_places(self):
        import inspect
        m = self._meta()
        assert "36" in inspect.getsource(m._bb_outage)
        assert O.FRESH_HOURS == 36


class TestAnEmptyPageNeverWipesHistory:
    """What an outage must not be able to do: with BB serving a maintenance
    page every parser returns nothing, and the store then has to leave the
    existing record alone. replace_daily_rows deletes rows the page no longer
    has, which is correct for a live table and catastrophic if an empty or
    truncated response is ever treated as 'BB now has no rows'."""

    def _stored(self, s):
        for d in (D(2026, 10, 14), D(2026, 10, 15)):
            s.add(InterbankRepo(trade_date=d, num_deals=30, amount_crore=2979.0, tenor_min_days=1,
                                tenor_max_days=7, rate_min_pct=8.5, rate_max_pct=8.95,
                                war_pct=8.8, ingested_utc=NOW()))
        s.commit()

    def test_an_empty_page_deletes_nothing(self):
        from engines.pipeline import replace_daily_rows
        eng, s = _mem()
        self._stored(s)
        stats = replace_daily_rows(s, InterbankRepo, [], ())
        s.commit()
        assert stats == {"updated": 0, "inserted": 0, "deleted": 0, "unchanged": 0}
        assert s.query(InterbankRepo).count() == 2

    def test_a_page_covering_other_dates_leaves_these_alone(self):
        """A truncated response that returns only a newer day must not delete
        the older ones it does not mention."""
        from engines.pipeline import replace_daily_rows
        eng, s = _mem()
        self._stored(s)
        replace_daily_rows(s, InterbankRepo, [{
            "trade_date": D(2026, 10, 18), "num_deals": 5, "amount_crore": 100.0,
            "tenor_min_days": 1, "tenor_max_days": 7, "rate_min_pct": 8.0,
            "rate_max_pct": 9.0, "war_pct": 8.5, "ingested_utc": NOW()}], ())
        s.commit()
        assert {r.trade_date for r in s.query(InterbankRepo)} == {
            D(2026, 10, 14), D(2026, 10, 15), D(2026, 10, 18)}
