"""The desk PC is the only machine BB lets in; GitHub's blocked runs keep the refresh timestamp fresh. A silent PC must be
its own alarm, judged on the PC's own rows and in WORKING days (a weekend off is not an outage)."""
import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import validate


def _run(monkeypatch, pc_hours_ago, github_hours_ago=0.5):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime.datetime.utcnow()
    if pc_hours_ago is not None:
        s.add(dbmod.StepRun(run_utc=now - datetime.timedelta(hours=pc_hours_ago), machine="pc", step="callmoney", status="ok"))
    s.add(dbmod.StepRun(run_utc=now - datetime.timedelta(hours=github_hours_ago), machine="github", step="callmoney", status="blocked"))
    s.commit()
    monkeypatch.setattr(validate, "get_session", lambda: s)
    v = validate.integrity_check()
    return v


def test_a_recent_pc_row_is_quiet_even_when_github_is_blocked(monkeypatch):
    v = _run(monkeypatch, 1)
    assert not [x for x in v["issues"] + v["notices"] if x.startswith("pc-silent")]


def test_a_pc_silent_for_days_is_loud_though_github_keeps_running(monkeypatch):
    v = _run(monkeypatch, 24 * 6)               # six days: at least two working days whatever today is
    assert any(x.startswith("pc-silent") for x in v["issues"]), "GitHub's hourly runs must not hide a silent PC"


def test_a_pc_that_never_reported_is_noticed(monkeypatch):
    v = _run(monkeypatch, None)
    assert any(x.startswith("pc-silent") for x in v["notices"])
