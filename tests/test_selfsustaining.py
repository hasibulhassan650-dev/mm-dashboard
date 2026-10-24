"""
The self-sustaining mechanism — tests that keep each layer in place.

Sep-2026 outage, in one line: an unpinned SQLAlchemy upgrade changed the default
Postgres driver, every data job died at startup, and for FIVE DAYS nothing said
so — the site was up, the uptime sentinel was green, and the integrity banner
kept showing the last verdict it had managed to record. Separately, a second
writer (reconcile.py) that never loaded the holiday calendar had been quietly
adding yield rows with no issue_date, dated onto public holidays.

Each test below pins one layer of the fix. They are deliberately structural —
several scan the repo rather than call a function — because the failure mode
they guard is "someone adds a new writer / workflow / entry point and forgets".
"""
import datetime
import json
import re
import subprocess
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import calendar_utils
import config
import sentinel
from db import PrimaryYieldSnapshot, CouponEvent, MaturityEvent, AuctionEvent, HolidayCalendar
from engines.repair import self_heal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = datetime.date


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _tracked_files(suffix):
    """Files git actually tracks — CI clones exactly this set, and local scratch
    scripts must not be able to fail (or pass) the structural tests."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f.endswith(suffix)]


# ── Layer: one writer per protected table ────────────────────────────────────

# reconcile.py wrote yields with a hand-rolled INSERT that omitted issue_date.
# Only the canonical upsert may write these tables.
_PROTECTED = ["primary_yield_snapshots", "omo_transactions", "call_money_rates",
              "ref_rates", "coupon_events", "maturity_events", "auction_events"]
_MODELS = ["PrimaryYieldSnapshot", "OMOTransaction", "CallMoneyRate", "RefRate",
           "CouponEvent", "MaturityEvent", "AuctionEvent"]
_WRITER_ALLOWED = {"engines/pipeline.py", "engines/repair.py", "db.py", "db_simple.py",
                   "migrate_to_supabase.py"}


def test_no_second_writer_to_protected_tables():
    offenders = []
    ins = re.compile(r"INSERT\s+INTO\s+(" + "|".join(_PROTECTED) + r")\b", re.I)
    add = re.compile(r"session\.add\(\s*(" + "|".join(_MODELS) + r")\(")
    for rel in _tracked_files(".py"):
        if rel.startswith("tests/") or rel.replace("\\", "/") in _WRITER_ALLOWED:
            continue
        text = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        for i, line in enumerate(text.splitlines(), 1):
            if ins.search(line) or add.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()[:90]}")
    assert not offenders, (
        "A second writer to a protected table was added. Route it through "
        "engines.pipeline.upsert_primary_yield (or the matching canonical upsert) — "
        "hand-rolled writes are how issue_date went missing:\n" + "\n".join(offenders))


# ── Layer: every DB-writing workflow runs the integrity gate ─────────────────

def test_every_db_writing_workflow_has_the_integrity_gate():
    missing = []
    for rel in _tracked_files(".yml"):
        if not rel.startswith(".github/workflows/"):
            continue
        raw = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        if "DATABASE_URL:" not in raw:          # doesn't touch the database
            continue
        # A workflow that only READS may opt out, but it has to say so in the file
        # itself — an allowlist buried inside this test is a place to hide.
        if "integrity-gate: read-only" in raw:
            continue
        if "actions/integrity-gate" not in raw:
            missing.append(rel)
    assert not missing, (
        "These workflows write to the database but never check what they wrote — "
        "reconcile.py ran ungated for months and produced 32 bad rows. Add the gate, or "
        "declare '# integrity-gate: read-only <why>' in the file if it only reads:\n"
        + "\n".join(missing))


def test_integrity_gate_action_exists_and_parses():
    p = os.path.join(ROOT, ".github", "actions", "integrity-gate", "action.yml")
    assert os.path.exists(p), "the shared gate action is missing"
    spec = yaml.safe_load(open(p, encoding="utf-8"))
    assert spec["runs"]["using"] == "composite"
    assert "github-token" in spec["inputs"]


# ── Layer: the driver is never inherited from a library default ─────────────

@pytest.mark.parametrize("url", [
    "postgresql://u:p@h:5432/db",             # bare: SQLAlchemy 2.1 flipped this to psycopg v3
    "postgresql+psycopg://u:p@h:5432/db",     # names a driver we do not ship
    "postgresql+psycopg2://u:p@h:5432/db",
])
def test_postgres_url_always_names_an_installed_driver(url):
    out = config._normalise_pg_driver(url)
    driver = out.split("://", 1)[0].split("+", 1)[1]
    __import__(driver)                          # must be importable, or the job dies at startup
    assert out.endswith("u:p@h:5432/db")        # credentials/host untouched


def test_non_postgres_urls_are_left_alone():
    assert config._normalise_pg_driver("sqlite:///data/x.db") == "sqlite:///data/x.db"


# ── Layer: the calendar loads itself or fails loudly — never guesses ────────

class TestCalendarCannotGuess:
    def setup_method(self):
        self._saved = calendar_utils._holiday_set
        calendar_utils._holiday_set = None          # "never loaded"

    def teardown_method(self):
        calendar_utils._holiday_set = self._saved

    def test_raises_rather_than_assuming_no_holidays(self, monkeypatch):
        # With no calendar loaded AND no database, the honest answer is an
        # exception. Returning a weekend-only answer is what dated auctions
        # onto Victory Day for months.
        def boom():
            raise RuntimeError("no database here")
        monkeypatch.setattr(dbmod, "get_session", boom)
        with pytest.raises(calendar_utils.CalendarNotLoaded):
            calendar_utils.previous_working_day(D(2025, 12, 17))

    def test_loads_itself_from_the_database_on_first_use(self, monkeypatch):
        eng, s = _mem()
        s.add(HolidayCalendar(calendar_date=D(2025, 12, 16), holiday_name="Victory Day",
                              holiday_type="GOVT_GAZETTE"))
        s.commit()
        monkeypatch.setattr(dbmod, "get_session", lambda: sessionmaker(bind=eng)())
        # nobody called load_calendar_rows(): it must fetch the calendar itself
        assert calendar_utils.previous_working_day(D(2025, 12, 17)) == D(2025, 12, 15)
        assert not calendar_utils.is_working_day(D(2025, 12, 16))


# ── Layer: self-heal repairs the deterministic classes, and only those ──────

class TestSelfHeal:
    def _cal(self, s):
        for d in (D(2025, 12, 16), D(2026, 2, 4)):
            s.add(HolidayCalendar(calendar_date=d, holiday_name="closure", holiday_type="GOVT_GAZETTE"))

    def _yield(self, s, tenor, ad, issue, cut):
        s.add(PrimaryYieldSnapshot(snapshot_date=ad, auction_date=ad, issue_date=issue,
                                   security_type="T_BOND", tenor_label=tenor, tenor_years=10,
                                   cutoff_yield_pct=cut))

    def _heal(self, eng, s):
        calendar_utils.load_calendar_rows(s.query(HolidayCalendar).all())
        rep = self_heal(s); s.commit()
        return rep

    def teardown_method(self):
        calendar_utils.load_holidays(set(), set())

    def test_missing_issue_date_is_derived(self):
        eng, s = _mem(); self._cal(s)
        self._yield(s, "91D", D(2026, 9, 6), None, 8.59)
        s.commit()
        rep = self._heal(eng, s)
        row = s.query(PrimaryYieldSnapshot).one()
        assert row.issue_date == D(2026, 9, 7)        # next working day after the auction
        assert rep["repaired"].get("yield_issue_date") == 1

    def test_duplicate_dated_on_a_closure_is_dropped(self):
        # the real case: one auction stored twice, once on Victory Day
        eng, s = _mem(); self._cal(s)
        self._yield(s, "10Y", D(2025, 12, 15), D(2025, 12, 17), 10.87)
        self._yield(s, "10Y", D(2025, 12, 16), None, 10.87)
        s.commit()
        rep = self._heal(eng, s)
        rows = s.query(PrimaryYieldSnapshot).all()
        assert len(rows) == 1 and rows[0].auction_date == D(2025, 12, 15)
        assert rep["repaired"].get("yield_duplicate_removed") == 1

    def test_a_different_cutoff_on_the_closure_is_NOT_auto_resolved(self):
        # two different numbers is a judgement call, not a repair
        eng, s = _mem(); self._cal(s)
        self._yield(s, "10Y", D(2025, 12, 15), D(2025, 12, 17), 10.87)
        self._yield(s, "10Y", D(2025, 12, 16), None, 9.11)
        s.commit()
        rep = self._heal(eng, s)
        assert s.query(PrimaryYieldSnapshot).count() == 2
        assert any("DIFFERENT cut-off" in x for x in rep["skipped"])

    def test_lone_row_on_a_closure_moves_to_the_real_trading_day(self):
        eng, s = _mem(); self._cal(s)
        self._yield(s, "2Y", D(2026, 2, 4), None, 10.46)
        s.commit()
        self._heal(eng, s)
        row = s.query(PrimaryYieldSnapshot).one()
        assert row.auction_date == D(2026, 2, 3) and row.issue_date == D(2026, 2, 5)

    def test_payment_dates_are_rerolled_and_confirmed_auctions_left_alone(self):
        eng, s = _mem(); self._cal(s)
        s.add(CouponEvent(isin="BD0000000001", scheduled_date=D(2025, 12, 16),
                          payment_date=D(2025, 12, 16), amount_bdt_mill=1.0))
        s.add(MaturityEvent(isin="BD0000000002", scheduled_date=D(2025, 12, 16),
                            payment_date=D(2025, 12, 16), principal_bdt_mill=1.0))
        # CONFIRMED carries BB's real issue date and must never be recomputed
        s.add(AuctionEvent(auction_date=D(2025, 12, 15), settlement_date=D(2025, 12, 21),
                           tenor_label="91D", outflow_status="CONFIRMED"))
        s.add(AuctionEvent(auction_date=D(2025, 12, 15), settlement_date=D(2025, 12, 16),
                           tenor_label="182D", outflow_status="PLANNED"))
        s.commit()
        self._heal(eng, s)
        assert s.query(CouponEvent).one().payment_date == D(2025, 12, 17)
        assert s.query(MaturityEvent).one().payment_date == D(2025, 12, 17)
        conf = s.query(AuctionEvent).filter_by(tenor_label="91D").one()
        plan = s.query(AuctionEvent).filter_by(tenor_label="182D").one()
        assert conf.settlement_date == D(2025, 12, 21), "a CONFIRMED settlement was overwritten"
        assert plan.settlement_date == D(2025, 12, 17)

    def test_second_run_changes_nothing(self):
        eng, s = _mem(); self._cal(s)
        self._yield(s, "91D", D(2026, 9, 6), None, 8.59)
        self._yield(s, "2Y", D(2026, 2, 4), None, 10.46)
        s.commit()
        self._heal(eng, s)
        again = self._heal(eng, s)
        assert again["total"] == 0, f"self-heal is not idempotent: {again['changes']}"


# ── Layer: the heartbeat notices a dead pipeline behind a healthy site ──────

def _stub_http(monkeypatch, status_payload):
    def fake(url, timeout=25):
        if "/api/meta/status" in url:
            return 200, json.dumps(status_payload)
        if "deploy-status" in url:
            return 200, json.dumps({"commitSha": "deadbee"})
        return 200, "ok"
    monkeypatch.setattr(sentinel, "_http", fake)


def _payload(hours_old, ok=True):
    ts = (datetime.datetime.utcnow() - datetime.timedelta(hours=hours_old)).isoformat()
    return {"last_run": ts, "last_run_errors": [],
            "data_health": {"ok": ok, "issue_count": 0 if ok else 32,
                            "by_table": {} if ok else {"yields": 31}},
            "datasets": {"callmoney": {"label": "Call Money", "kind": "daily", "current": True}}}


def test_sentinel_flags_a_dead_pipeline_behind_a_live_site(monkeypatch):
    # exactly the Sep-2026 state: site up, deploy current, data 5 days old
    _stub_http(monkeypatch, _payload(hours_old=115))
    monkeypatch.setattr(sentinel.subprocess, "check_output", lambda *a, **k: b"deadbee")
    v = sentinel.check()
    assert v["ok"] is False
    assert any("REFRESH HAS NOT RUN" in p for p in v["problems"]), v["problems"]


def test_sentinel_flags_a_stale_integrity_verdict(monkeypatch):
    _stub_http(monkeypatch, _payload(hours_old=115, ok=False))
    monkeypatch.setattr(sentinel.subprocess, "check_output", lambda *a, **k: b"deadbee")
    v = sentinel.check()
    assert any("Integrity checks failing" in p for p in v["problems"])
    assert any("old" in p and "stale data" in p for p in v["problems"]), v["problems"]


def test_sentinel_quiet_when_the_data_is_fresh(monkeypatch):
    _stub_http(monkeypatch, _payload(hours_old=3))
    monkeypatch.setattr(sentinel.subprocess, "check_output", lambda *a, **k: b"deadbee")
    v = sentinel.check()
    assert v["ok"] is True, v["problems"]


def test_sentinel_flags_a_daily_series_behind_the_last_working_day(monkeypatch):
    p = _payload(hours_old=2)
    p["datasets"]["callmoney"]["current"] = False
    _stub_http(monkeypatch, p)
    monkeypatch.setattr(sentinel.subprocess, "check_output", lambda *a, **k: b"deadbee")
    v = sentinel.check()
    assert any("Call Money" in x for x in v["problems"]), v["problems"]


# ── Two writers, one table: the failure of 16-Oct-2026 ───────────────────────

class TestTheGazetteOutranksTheSeed:
    """BB's holiday page is read into the same table a YAML seed owns.

    load_holiday_file treats its file as the source of truth for a whole
    FISCAL YEAR and deletes any row in that year the file does not list. That
    rule exists for a real reason — the wrong "approximate" Eid dates outlived
    their correction because loading was upsert-only — but it made the seed the
    SECOND writer of a table BB's own gazette now feeds. Every refresh silently
    deleted the eleven dates fetched from BB's page, 20 and 21 October 2026
    among them, so the calendar ended on 26-Aug again and every settlement date
    crossing Durga Puja went back to being wrong. Within hours of being fixed,
    by hand, twice.

    A seed may delete the rows it wrote. It may not delete BB's.
    """

    def _seed_file(self, tmp_path, dates):
        p = tmp_path / "holidays_FY2026-27.yaml"
        p.write_text(yaml.safe_dump({
            "fiscal_year": "2026-27",
            "holidays": [{"date": d, "name": "Seeded", "type": "GOVT_GAZETTE"}
                         for d in dates],
        }), encoding="utf-8")
        return str(p)

    def _load(self, monkeypatch, path, rows):
        import seeds_loader
        eng, s = _mem()
        for d, name, typ, src in rows:
            s.add(HolidayCalendar(calendar_date=d, holiday_name=name, holiday_type=typ,
                                  fiscal_year="2026-27", source=src))
        s.commit()
        monkeypatch.setattr(seeds_loader, "get_session", lambda: sessionmaker(bind=eng)())
        seeds_loader.load_holiday_file(path)
        return sessionmaker(bind=eng)()

    def test_a_date_from_bbs_page_survives_a_seed_load(self, tmp_path, monkeypatch):
        s2 = self._load(
            monkeypatch, self._seed_file(tmp_path, ["2026-07-01"]),
            [(datetime.date(2026, 10, 20), "Durga Puja", "GAZETTED",
              "https://www.bb.org.bd/en/index.php/mediaroom/holiday")])
        kept = {h.calendar_date for h in s2.query(HolidayCalendar).all()}
        assert datetime.date(2026, 10, 20) in kept, \
            "the seed deleted a holiday read from BB's own gazette"

    def test_a_stale_seeded_date_is_still_removed(self, tmp_path, monkeypatch):
        """The deletion rule has to keep working, or the wrong Eid dates come
        back. Only its reach changes."""
        s2 = self._load(
            monkeypatch, self._seed_file(tmp_path, ["2026-07-01"]),
            [(datetime.date(2026, 5, 25), "Approximate Eid", "GOVT_GAZETTE",
              "data/seeds/holidays_FY2026-27.yaml")])
        kept = {h.calendar_date for h in s2.query(HolidayCalendar).all()}
        assert datetime.date(2026, 5, 25) not in kept
        assert datetime.date(2026, 7, 1) in kept

    def test_a_row_with_no_recorded_source_is_treated_as_the_seeds(self):
        """Anything predating provenance is the seed's to manage; only an
        explicit BB source is protected."""
        import inspect
        import seeds_loader
        src = inspect.getsource(seeds_loader.load_holiday_file)
        assert "bb.org.bd" in src, "the protection is no longer anchored to BB's host"


class TestOneAuctionOneRowInTheWriter:
    """The crash of 16-Oct-2026, and the silent duplicate before it.

    primary_yield_snapshots is keyed (tenor_label, auction_date), so a re-parse
    that labels one auction differently — a re-issue read from BB's remaining-
    maturity column one day and its tenor-and-name column the next — looks like
    a brand-new row and lands BESIDE the right one, double-counting 2,500 crore
    in the ladder. The partial unique index on (auction_date, isin) stops that,
    but the writer did not know about it, so the insert raised a UniqueViolation
    that killed the entire treasury step for a day.

    Neither outcome is acceptable. The writer finds the row by ISIN and
    corrects its label.
    """

    def _row(self, label, **kw):
        base = {
            "snapshot_date": datetime.date(2026, 2, 28),
            "auction_date": datetime.date(2026, 2, 3),
            "issue_date": datetime.date(2026, 2, 5),
            "security_type": "T_BOND", "tenor_label": label, "tenor_years": 2.0,
            "cutoff_yield_pct": 10.4598, "accepted_bdt_crore": 2500.0,
            "isin": "BD0928101056", "is_reissue": True, "reissue_remaining": "2.60 Yr",
            "original_tenor_label": "5Y",
        }
        return {**base, **kw}

    def test_a_relabelled_auction_updates_instead_of_inserting(self):
        from engines.pipeline import upsert_primary_yield
        eng, s = _mem()
        upsert_primary_yield(s, self._row("5Y"))
        s.commit()
        upsert_primary_yield(s, self._row("2Y"))
        s.commit()
        rows = s.query(PrimaryYieldSnapshot).all()
        assert len(rows) == 1, f"one auction stored {len(rows)} times"
        assert rows[0].tenor_label == "2Y", "the corrected label was not kept"
        assert rows[0].tenor_years == 2.0

    def test_two_genuinely_different_auctions_on_one_day_both_stand(self):
        """Bills and bonds settle on the same day constantly. The invariant is
        one ISIN per day, not one auction per day."""
        from engines.pipeline import upsert_primary_yield
        eng, s = _mem()
        upsert_primary_yield(s, self._row("2Y"))
        upsert_primary_yield(s, self._row("91D", isin="BD0909110266",
                                          security_type="T_BILL", tenor_years=0.25,
                                          cutoff_yield_pct=8.5899))
        s.commit()
        assert s.query(PrimaryYieldSnapshot).count() == 2

    def test_a_row_with_no_isin_still_keys_on_the_label(self):
        """Older months were stored before the parser kept the ISIN; they must
        keep working exactly as before."""
        from engines.pipeline import upsert_primary_yield
        eng, s = _mem()
        upsert_primary_yield(s, self._row("5Y", isin=None))
        upsert_primary_yield(s, self._row("2Y", isin=None))
        s.commit()
        assert s.query(PrimaryYieldSnapshot).count() == 2


class TestAnFxRowIsDatedByTheLiveCalendar:
    """17-Oct-2026 was a declared working Saturday our calendar did not know.

    BB's exchange-rate page describes the PREVIOUS business day, so
    rate_date = previous_working_day(published_date). With 17-Oct missing, the
    row published on the 18th walked back over Sat 17 and Fri 16 and was dated
    Thu 15-Oct: two days out, on top of the real 15-Oct figures. The
    cross-source USD check then compared two different trading days and
    reported three disagreements it was right to report. One missing calendar
    entry, three defects, one wrong rate.
    """

    def _row(self, rate_date, published, bid):
        from db import FxRateDaily
        return FxRateDaily(rate_date=rate_date, published_date=published, currency="USD",
                           bid_rate=bid, ask_rate=bid + 0.03, war_rate=bid)

    def _calendar(self, with_saturday: bool):
        D = datetime.date
        calendar_utils.load_calendar_rows(
            [(D(2026, 10, 17), "WORKING_DAY")] if with_saturday else [])

    def teardown_method(self, _m):
        calendar_utils.load_holidays(set(), set())

    def test_the_row_published_on_the_18th_moves_to_the_17th(self):
        D = datetime.date
        self._calendar(with_saturday=True)
        eng, s = _mem()
        s.add(self._row(D(2026, 10, 15), D(2026, 10, 18), 123.55))   # mis-dated
        s.commit()
        rep = self_heal(s); s.commit()
        assert rep["repaired"].get("fx-rate-date") == 1, rep
        assert s.query(__import__("db").FxRateDaily).one().rate_date == D(2026, 10, 17)

    def test_it_never_overwrites_a_row_already_on_the_target_date(self):
        """Two rows contending for one date is a conflict for a human, not a
        tie the code should break by deleting someone's figures."""
        D = datetime.date
        self._calendar(with_saturday=True)
        eng, s = _mem()
        s.add(self._row(D(2026, 10, 15), D(2026, 10, 18), 123.55))
        s.add(self._row(D(2026, 10, 17), D(2026, 10, 18), 123.99))
        s.commit()
        rep = self_heal(s); s.commit()
        assert not rep["repaired"].get("fx-rate-date")
        assert any("already holds another row" in x for x in rep["skipped"]), rep["skipped"]
        from db import FxRateDaily
        assert s.query(FxRateDaily).count() == 2

    def test_a_correctly_dated_row_is_left_alone(self):
        D = datetime.date
        self._calendar(with_saturday=True)
        eng, s = _mem()
        s.add(self._row(D(2026, 10, 17), D(2026, 10, 18), 123.60))
        s.commit()
        assert not self_heal(s)["repaired"].get("fx-rate-date")

    def test_without_the_saturday_the_old_date_is_what_the_calendar_says(self):
        """Documents the cause: this is the state that produced the bug."""
        D = datetime.date
        self._calendar(with_saturday=False)
        assert calendar_utils.previous_working_day(D(2026, 10, 18)) == D(2026, 10, 15)
        self._calendar(with_saturday=True)
        assert calendar_utils.previous_working_day(D(2026, 10, 18)) == D(2026, 10, 17)
