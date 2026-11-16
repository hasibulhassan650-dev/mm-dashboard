"""
One row BB prints against itself must cost that row's bad fields, never its day or its month.

The incident (11-Nov-2026): BB printed a repo row with range 8.90-8.85 and weighted average 8.92, outside it. The database
CHECK ck_repo_rate_band refused it and ONE flush for the whole month threw away 10, 12 and 15 Nov with it, for four days.
engines.pipeline.store_rows_isolated writes each row in its own savepoint, blanks exactly the fields the violated
constraint names (db.CONSTRAINT_FIELDS) and quarantines whatever still cannot be stored. Nothing is dropped silently.

SQLite does not carry the production CHECKs (they are Postgres migrations in db.init_db), so these tests declare a small
throwaway model with the same named constraint.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import CheckConstraint, Column, Date, DateTime, Float, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import db as dbmod
from engines.pipeline import (constraint_name, replace_daily_rows, store_rows_isolated)

D = datetime.date
TBase = declarative_base()


class TRepo(TBase):
    """A repo-shaped table carrying the production constraint under its production name."""
    __tablename__ = "t_repo"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    num_deals = Column(Integer)
    amount_crore = Column(Float)
    rate_min_pct = Column(Float)
    rate_max_pct = Column(Float)
    war_pct = Column(Float)
    mystery = Column(Float)
    __table_args__ = (
        CheckConstraint("rate_min_pct <= war_pct AND war_pct <= rate_max_pct", name="ck_repo_rate_band"),
        # a rule db.CONSTRAINT_FIELDS knows nothing about
        CheckConstraint("mystery IS NULL OR mystery > 0", name="ck_not_in_registry"),
    )


class TStrictRepo(TBase):
    """Same band rule, but the weighted average may not be blank: blanking cannot make the row storable."""
    __tablename__ = "t_strict_repo"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    num_deals = Column(Integer)
    rate_min_pct = Column(Float)
    rate_max_pct = Column(Float)
    war_pct = Column(Float, nullable=False)
    __table_args__ = (CheckConstraint("rate_min_pct <= war_pct AND war_pct <= rate_max_pct", name="ck_repo_rate_band"),)


class _TReproduce(TBase):
    """Carries an ingested_utc column so the volatile-field dedupe can be exercised."""
    __tablename__ = "t_reproduce"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True)
    num_deals = Column(Integer)
    amount_crore = Column(Float)
    rate_min_pct = Column(Float)
    rate_max_pct = Column(Float)
    war_pct = Column(Float)
    mystery = Column(Float)
    ingested_utc = Column(DateTime)
    __table_args__ = (CheckConstraint("mystery IS NULL OR mystery > 0", name="ck_not_in_registry"),)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)      # quarantined_rows lives here
    TBase.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _row(d, **kw):
    r = {"trade_date": d, "num_deals": 40, "amount_crore": 3000.0,
         "rate_min_pct": 8.80, "rate_max_pct": 8.90, "war_pct": 8.85}
    r.update(kw)
    return r


# BB's own 11-Nov row: range printed 8.90-8.85 (low above high), average 8.92 outside it.
def _incident_rows():
    return [_row(D(2026, 11, 10)),
            _row(D(2026, 11, 11), num_deals=59, amount_crore=5629.689717845,
                 rate_min_pct=8.90, rate_max_pct=8.85, war_pct=8.92),
            _row(D(2026, 11, 12)),
            _row(D(2026, 11, 15))]


class TestConstraintName:
    def test_postgres_message(self):
        msg = ('(psycopg2.errors.CheckViolation) new row for relation "interbank_repo" violates check constraint '
               '"ck_repo_rate_band"\nDETAIL:  Failing row contains (...).')
        assert constraint_name(Exception(msg)) == "ck_repo_rate_band"

    def test_sqlite_message(self):
        assert constraint_name(Exception("CHECK constraint failed: ck_repo_rate_band")) == "ck_repo_rate_band"

    def test_a_message_that_names_nothing_gives_none_so_the_row_is_quarantined_not_guessed(self):
        assert constraint_name(Exception("UNIQUE constraint failed: t_repo.trade_date")) is None
        assert constraint_name(Exception("NOT NULL constraint failed: t_repo.war_pct")) is None


class TestTheIncident:
    def test_the_good_days_are_stored_and_only_the_constraints_fields_are_blank(self):
        s = _mem()
        out = store_rows_isolated(s, TRepo, _incident_rows(), "repo")
        s.commit()
        assert out["stored"] == 4 and out["quarantined"] == []
        by_day = {r.trade_date.day: r for r in s.query(TRepo)}
        assert sorted(by_day) == [10, 11, 12, 15], "the three good days must not be lost with the bad one"
        bad = by_day[11]
        fields = dbmod.CONSTRAINT_FIELDS["ck_repo_rate_band"]
        # which of BB's three numbers is wrong cannot be known: none is stored, none is invented
        assert [getattr(bad, f) for f in fields] == [None, None, None]
        # the facts the constraint says nothing about survive
        assert bad.num_deals == 59 and bad.amount_crore == 5629.689717845
        good = by_day[12]
        assert (good.rate_min_pct, good.rate_max_pct, good.war_pct) == (8.80, 8.90, 8.85), "a good row is untouched"

    def test_the_blanked_row_is_reported_with_its_original_values(self):
        out = store_rows_isolated(_mem(), TRepo, _incident_rows(), "repo")
        assert len(out["blanked"]) == 1
        b = out["blanked"][0]
        assert b["constraint"] == "ck_repo_rate_band"
        assert set(b["fields"]) == set(dbmod.CONSTRAINT_FIELDS["ck_repo_rate_band"])
        assert b["original"] == {"rate_min_pct": 8.90, "rate_max_pct": 8.85, "war_pct": 8.92}
        assert b["row"]["trade_date"] == "2026-11-11", "dates are reported as ISO strings"

    def test_a_row_the_constraint_does_not_touch_is_never_altered(self):
        s = _mem()
        store_rows_isolated(s, TRepo, [_row(D(2026, 11, 10), mystery=None)], "repo")
        s.commit()
        r = s.query(TRepo).one()
        assert (r.num_deals, r.amount_crore, r.rate_min_pct) == (40, 3000.0, 8.80)

    def test_replace_daily_rows_keeps_the_day_of_a_bad_row(self):
        # the live path: before, the bad row cost its whole day (stats["rejected"]); now it costs its blank fields only
        s = _mem()
        st = replace_daily_rows(s, TRepo, _incident_rows(), ())
        s.commit()
        assert "rejected" not in st and "quarantined" not in st
        assert st["inserted"] == 4
        assert len(st["blanked"]) == 1 and st["blanked"][0]["row"]["trade_date"] == "2026-11-11"
        bad = s.query(TRepo).filter_by(trade_date=D(2026, 11, 11)).one()
        assert bad.war_pct is None and bad.num_deals == 59

    def test_replace_daily_rows_blanks_an_update_too(self):
        s = _mem()
        replace_daily_rows(s, TRepo, [_row(D(2026, 11, 11))], ()); s.commit()
        st = replace_daily_rows(s, TRepo, [_row(D(2026, 11, 11), rate_min_pct=8.90, rate_max_pct=8.85, war_pct=8.92)], ())
        s.commit()
        assert st["updated"] == 1 and len(st["blanked"]) == 1
        r = s.query(TRepo).one()
        assert (r.rate_min_pct, r.rate_max_pct, r.war_pct) == (None, None, None)
        assert r.num_deals == 40


class TestNothingIsLostSilently:
    def test_an_unknown_constraint_quarantines_the_raw_row(self):
        s = _mem()
        bad = _row(D(2026, 11, 11), mystery=-1.0)
        out = store_rows_isolated(s, TRepo, [_row(D(2026, 11, 10)), bad], "repo")
        s.commit()
        assert out["stored"] == 1 and out["blanked"] == [] and len(out["quarantined"]) == 1
        assert out["quarantined"][0]["constraint"] == "ck_not_in_registry"
        q = s.query(dbmod.QuarantinedRow).one()
        assert (q.step, q.table_name, q.constraint) == ("repo", "t_repo", "ck_not_in_registry")
        raw = json.loads(q.raw_json)
        assert raw["trade_date"] == "2026-11-11" and raw["mystery"] == -1.0 and raw["num_deals"] == 40
        assert s.query(TRepo).count() == 1, "the quarantined row is not in the table"

    def test_a_retry_that_still_fails_is_quarantined_not_dropped(self):
        s = _mem()
        out = store_rows_isolated(s, TStrictRepo, [
            {"trade_date": D(2026, 11, 10), "num_deals": 5, "rate_min_pct": 8.8, "rate_max_pct": 8.9, "war_pct": 8.85},
            {"trade_date": D(2026, 11, 11), "num_deals": 59, "rate_min_pct": 8.9, "rate_max_pct": 8.85, "war_pct": 8.92},
        ], "repo")
        s.commit()
        assert out["stored"] == 1 and out["blanked"] == []
        assert len(out["quarantined"]) == 1
        assert out["quarantined"][0]["constraint"] == "ck_repo_rate_band"
        assert json.loads(s.query(dbmod.QuarantinedRow).one().raw_json)["war_pct"] == 8.92, "the raw print is kept as BB gave it"

    def test_stored_plus_quarantined_equals_input(self):
        s = _mem()
        rows = [
            _row(D(2026, 11, 9)),                                       # good
            _row(D(2026, 11, 10), war_pct=9.5),                         # blanked
            _row(D(2026, 11, 11), mystery=-3.0),                        # unknown constraint
            _row(D(2026, 11, 9)),                                       # unique(trade_date) clash: no constraint name
            _row(D(2026, 11, 12)),                                      # good, after the failures
        ]
        out = store_rows_isolated(s, TRepo, rows, "repo")
        s.commit()
        assert out["stored"] + len(out["quarantined"]) == len(rows)
        assert out["stored"] == s.query(TRepo).count() == 3
        assert s.query(dbmod.QuarantinedRow).count() == len(out["quarantined"]) == 2
        assert len(out["blanked"]) == 1 and out["blanked"][0]["row"]["trade_date"] == "2026-11-10"

    def test_the_same_bad_row_read_every_hour_is_quarantined_once(self):
        # the incident logged the same error 32 times; a row re-read unchanged must not pile up
        s = _mem()
        for hour in range(3):
            bad = _row(D(2026, 11, 11), mystery=-1.0)
            bad["ingested_utc"] = datetime.datetime(2026, 11, 16, hour)   # volatile
            store_rows_isolated(s, _TReproduce, [bad], "repo")
            s.commit()
        assert s.query(dbmod.QuarantinedRow).count() == 1

    def test_a_day_the_database_refuses_even_blank_is_rejected_and_its_rows_quarantined(self):
        s = _mem()
        d1, d2, d3 = D(2026, 11, 10), D(2026, 11, 11), D(2026, 11, 12)
        good = lambda d: {"trade_date": d, "num_deals": 5, "amount_crore": 100.0}
        st = replace_daily_rows(s, TRepo, [good(d1), good(d2), good(d2), good(d3)], ())
        s.commit()
        assert st["rejected"] == [str(d2)]
        assert sorted(r.trade_date for r in s.query(TRepo)) == [d1, d3]
        assert len(st["quarantined"]) == 1, "the refused row is recorded, not just counted"
        assert json.loads(s.query(dbmod.QuarantinedRow).one().raw_json)["trade_date"] == "2026-11-11"


# ── The registry cannot fall behind the schema ───────────────────────────────

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db.py")


def _constraint_names(source: str) -> set:
    """Every CHECK constraint name a db.py source creates: model CheckConstraint(..., name='x') and ADD CONSTRAINT x."""
    names = set(re.findall(r"CheckConstraint\([^)]*?name\s*=\s*['\"](\w+)['\"]", source, flags=re.S))
    names |= set(re.findall(r"ADD CONSTRAINT\s+(\w+)", source))
    return names


class TestEveryConstraintHasAnEntry:
    def test_the_scanner_sees_both_forms(self):
        # a scanner that silently finds nothing would make the guard below pass vacuously
        src = ('CheckConstraint("a <= b", name="ck_new_model")\n'
               '"ALTER TABLE t ADD CONSTRAINT ck_new_migration CHECK (a <= b) NOT VALID"')
        assert _constraint_names(src) == {"ck_new_model", "ck_new_migration"}
        found = _constraint_names(open(_SRC, encoding="utf-8").read())
        assert "ck_repo_rate_band" in found and len(found) >= 8

    def test_every_check_constraint_in_db_py_is_in_constraint_fields(self):
        # WHY: a constraint without an entry cannot be blanked, so the next BB row that breaks it is quarantined whole
        # and its day is refused: the 11-Nov repo incident again. Add the new constraint's columns to CONSTRAINT_FIELDS.
        found = _constraint_names(open(_SRC, encoding="utf-8").read())
        missing = sorted(found - set(dbmod.CONSTRAINT_FIELDS))
        assert not missing, f"CHECK constraint(s) without a CONSTRAINT_FIELDS entry in db.py: {missing}"

    def test_no_entry_names_a_constraint_that_does_not_exist(self):
        found = _constraint_names(open(_SRC, encoding="utf-8").read())
        assert not (set(dbmod.CONSTRAINT_FIELDS) - found), "stale CONSTRAINT_FIELDS entry"

    def test_every_listed_field_is_a_real_column(self):
        # a typo here would make the blank-and-retry set an attribute the model does not have
        columns = {c.name for t in dbmod.Base.metadata.tables.values() for c in t.columns}
        for name, fields in dbmod.CONSTRAINT_FIELDS.items():
            assert fields and set(fields) <= columns, (name, fields)
