"""The desk's own entries (desk marks, recorded absences, news-derived events) cannot be re-read from BB and nothing backed them up.
A backup is worth nothing unless it can be verified and restored, so the round trip is the test."""
import datetime
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import backup_db as B
import db as dbmod

D = datetime.date(2026, 11, 21)


def _engine(path):
    eng = create_engine(f"sqlite:///{path}")
    dbmod.Base.metadata.create_all(eng)
    return eng


def _seed(eng):
    s = sessionmaker(bind=eng)()
    s.add(dbmod.DeskMark(auction_date=datetime.date(2026, 11, 18), tenor_label="5Y", security_type="T_BOND", cutoff_yield_pct=10.01,
                         accepted_crore=1500.0, entered_by="desk", note="seen at the auction"))
    s.commit()


def test_a_backup_verifies_restores_and_a_flipped_byte_is_caught(tmp_path):
    src = _engine(tmp_path / "a.db")
    _seed(src)
    r = B.run_backup(today=D, root=tmp_path / "bk", engine=src)
    assert r["rows"] >= 1 and not r["skipped"]
    folder = tmp_path / "bk" / D.isoformat()
    assert B.verify(folder) == []
    m = json.loads((folder / "manifest.json").read_text())["tables"]
    assert m["desk_marks"]["rows"] == 1

    dst = _engine(tmp_path / "b.db")
    assert B.restore(folder, ["desk_marks"], apply=False, engine=dst) == {"desk_marks": 1}      # dry run counts, writes nothing
    assert dst.connect().execute(text("SELECT COUNT(*) FROM desk_marks")).scalar() == 0
    B.restore(folder, ["desk_marks"], apply=True, engine=dst)
    row = dst.connect().execute(text("SELECT tenor_label, cutoff_yield_pct, note FROM desk_marks")).fetchone()
    assert tuple(row) == ("5Y", 10.01, "seen at the auction")
    assert B.restore(folder, ["desk_marks"], apply=True, engine=dst) == {"desk_marks": 0}        # idempotent: never doubles a row

    f = folder / "desk_marks.csv.gz"
    f.write_bytes(f.read_bytes()[:-3] + b"xyz")
    assert B.verify(folder) and pytest.raises(RuntimeError, B.restore, folder, ["desk_marks"], True, dst)


def test_todays_backup_is_taken_once_and_old_ones_are_pruned(tmp_path):
    src = _engine(tmp_path / "a.db")
    for i in range(B.KEEP_DAYS + 3):
        B.run_backup(today=D + datetime.timedelta(days=i), root=tmp_path / "bk", engine=src)
    assert B.run_backup(today=D + datetime.timedelta(days=B.KEEP_DAYS + 2), root=tmp_path / "bk", engine=src)["skipped"]
    kept = sorted(p.name for p in (tmp_path / "bk").iterdir())
    assert len(kept) == B.KEEP_DAYS and kept[0] == (D + datetime.timedelta(days=3)).isoformat()
