"""
One OMO row the database refuses must cost that row (or its own day), never the other days.

The harm: _store_omo_txns used to write the whole batch in one flush. A single row the database refused (a CHECK, or a
duplicate line inside one release) threw away every OMO day around it, and the next hourly run hit the same row again,
while a correction that had already deleted the stored release could be left half-applied. Now each operation date is its
own savepoint (supersession and the re-homing guards inside it) and each row goes through _write_isolated.

SQLite carries no production CHECKs (they are Postgres migrations in db.init_db), so a trigger raises the production
constraint's own message for a maturity before its transaction date.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import OMOTransaction, QuarantinedRow
from engines.failures import DB_CONSTRAINT, classify
from engines.pipeline import _store_omo_txns, omo_store_errors

D = datetime.date
NOW = datetime.datetime(2026, 11, 20, 6, 0)

_TRIGGERS = (
    "CREATE TRIGGER trg_omo_ck_ins BEFORE INSERT ON omo_transactions "
    "WHEN NEW.maturity_date IS NOT NULL AND NEW.maturity_date < NEW.transaction_date "
    "BEGIN SELECT RAISE(ABORT, 'CHECK constraint failed: ck_omo_maturity_after_txn'); END",
    "CREATE TRIGGER trg_omo_ck_upd BEFORE UPDATE ON omo_transactions "
    "WHEN NEW.maturity_date IS NOT NULL AND NEW.maturity_date < NEW.transaction_date "
    "BEGIN SELECT RAISE(ABORT, 'CHECK constraint failed: ck_omo_maturity_after_txn'); END",
)


@pytest.fixture
def sess():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    with eng.begin() as c:
        for t in _TRIGGERS:
            c.execute(text(t))
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _row(day, instr, tenor, acc, pub, serial, maturity=None):
    return {"transaction_date": day, "maturity_date": maturity or day + datetime.timedelta(days=tenor),
            "instrument": instr, "tenor_label": f"{tenor}D", "tenor_days": tenor, "accepted_bdt_crore": acc,
            "maturity_bdt_crore": 0.0, "rate_pct": None, "rate_range": None, "direction": "INJECTION",
            "source_pdf": f"pr_{serial}.pdf", "source_pub_date": pub, "source_serial": serial}


def _stored(s, day):
    return sorted((r.instrument, r.tenor_days, r.accepted_bdt_crore, r.source_serial)
                  for r in s.query(OMOTransaction).filter_by(transaction_date=day).all())


def _store(s, rows):
    stats: dict = {}
    saved, superseded = _store_omo_txns(s, rows, NOW, stats)
    s.commit()
    return saved, superseded, stats


def test_the_trigger_really_raises_the_production_constraint(sess):
    """The harness must be able to fail: without this the tests below would pass against a database that refuses nothing."""
    sess.add(OMOTransaction(transaction_date=D(2026, 11, 10), maturity_date=D(2026, 11, 9), instrument="SDF",
                            tenor_days=1, accepted_bdt_crore=5.0))
    with pytest.raises(IntegrityError) as ei:
        sess.flush()
    assert "ck_omo_maturity_after_txn" in str(ei.value)


def test_one_refused_omo_row_does_not_lose_the_other_days(sess):
    """A release that prints the same line twice (a unique violation no blanking can fix) refuses ITS day only."""
    d1, d2, d3 = D(2026, 11, 9), D(2026, 11, 10), D(2026, 11, 11)
    dup = _row(d2, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500")
    rows = [_row(d1, "SDF", 1, 10.0, D(2026, 11, 10), "05/2026-499"),
            dup, dict(dup), _row(d2, "IBLF", 7, 50.0, D(2026, 11, 11), "05/2026-500"),
            _row(d3, "SDF", 1, 30.0, D(2026, 11, 12), "05/2026-501")]
    saved, superseded, stats = _store(sess, rows)
    assert _stored(sess, d1) and _stored(sess, d3), "a refused day took its neighbours with it"
    assert _stored(sess, d2) == [], "a refused day must be stored whole or not at all"
    assert saved == 2 and superseded == 0
    assert stats["rejected"] == [str(d2)]
    assert len(stats["quarantined"]) == 1
    assert sess.query(QuarantinedRow).filter_by(step="omo").count() == 1


def test_one_blanked_omo_row_keeps_its_day_and_every_other_row(sess):
    """A maturity before its own transaction date is blanked on that field only; the rest of the release is stored."""
    d = D(2026, 11, 10)
    bad = _row(d, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500", maturity=D(2026, 11, 9))
    saved, _, stats = _store(sess, [bad, _row(d, "IBLF", 7, 50.0, D(2026, 11, 11), "05/2026-500")])
    assert saved == 2 and "rejected" not in stats and len(stats["blanked"]) == 1
    got = {r.instrument: r.maturity_date for r in sess.query(OMOTransaction).all()}
    assert got["SDF"] is None and got["IBLF"] == D(2026, 11, 17)


def test_refused_correction_never_half_replaces_the_stored_release(sess):
    """The supersession deletes the stored release then inserts the correction. If the correction is refused the day must
    roll back to the stored release, not be left empty or half-replaced."""
    d = D(2026, 11, 10)
    old = [_row(d, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500"),
           _row(d, "IBLF", 7, 50.0, D(2026, 11, 11), "05/2026-500"),
           _row(d, "SLF", 1, 20.0, D(2026, 11, 11), "05/2026-500")]
    _store(sess, old)
    before = _stored(sess, d)
    assert len(before) == 3
    fix = _row(d, "SDF", 1, 110.0, D(2026, 11, 13), "05/2026-512")
    saved, superseded, stats = _store(sess, [fix, dict(fix), _row(d, "IBLF", 7, 55.0, D(2026, 11, 13), "05/2026-512")])
    assert stats["rejected"] == [str(d)]
    assert _stored(sess, d) == before, "a refused correction destroyed or half-replaced the stored release"
    assert (saved, superseded) == (0, 0), "a rolled-back day must not be counted as saved or superseded"


def test_blanked_omo_maturity_is_reported_as_an_error_every_run(sess):
    """A tranche with no maturity date drops out of every outstanding query. It must be an ERROR on every hourly run (the
    re-read re-blanks it), attributed to omo and classified as a database-rule refusal, never a quiet warning."""
    d = D(2026, 11, 10)
    bad = _row(d, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500", maturity=D(2026, 11, 9))
    for run in range(3):
        _, _, stats = _store(sess, [dict(bad)])
        errs = omo_store_errors(stats)
        assert errs, f"run {run + 1}: a blanked maturity produced no error"
        assert all(e.startswith("omo:") for e in errs)
        assert "maturity_date" in " ".join(errs) and "ck_omo_maturity_after_txn" in " ".join(errs)
        assert all(classify(e, "omo") == DB_CONSTRAINT for e in errs)
        assert sess.query(OMOTransaction).filter_by(transaction_date=d).one().maturity_date is None
    assert omo_store_errors({}) == []


def test_a_refused_day_is_an_omo_error_classified_as_a_database_rule(sess):
    d = D(2026, 11, 10)
    r = _row(d, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500")
    _, _, stats = _store(sess, [r, dict(r)])
    errs = omo_store_errors(stats)
    assert len(errs) == 2 and errs[0].startswith("omo: BB rows for 2026-11-10")
    assert all(classify(e, "omo") == DB_CONSTRAINT for e in errs)


def test_hourly_reread_does_not_pile_up_quarantine(sess):
    """The same refused release is re-read every hour: it must stay ONE quarantined row, while still being reported."""
    d = D(2026, 11, 10)
    r = _row(d, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500")
    for _ in range(5):
        _, _, stats = _store(sess, [dict(r), dict(r)])
        assert stats["rejected"] == [str(d)]
    assert sess.query(QuarantinedRow).filter_by(step="omo").count() == 1


def test_an_unrelated_clean_batch_is_unchanged(sess):
    """No stats dict, two return values, nothing reported: the old calling convention (backfill_omo.py, other tests)."""
    d = D(2026, 11, 10)
    out = _store_omo_txns(sess, [_row(d, "SDF", 1, 100.0, D(2026, 11, 11), "05/2026-500")], NOW)
    sess.commit()
    assert out == (1, 0) and len(_stored(sess, d)) == 1
