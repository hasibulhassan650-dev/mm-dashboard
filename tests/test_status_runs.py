"""The Data Updates panel shows, per dataset, when its refresh step last RAN. That only means something if every
dataset maps to a step the pipeline really records - an unmapped one would read 'run not recorded' for ever."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "api"))

from routers import status_logic as S, meta
from engines.step_ledger import REPORTING_STEPS


def test_every_dataset_maps_to_a_step_the_ledger_records():
    assert set(S.STEP_OF) == set(meta._SOURCES), "a dataset was added without saying which step refreshes it"
    assert set(S.STEP_OF.values()) <= REPORTING_STEPS


def test_a_quiet_day_moves_the_run_not_the_data(monkeypatch):
    import datetime
    runs = {"callmoney": {"run": datetime.datetime(2026, 11, 20, 20, 0).isoformat(), "status": "ok", "machine": "pc",
                          "last_ok": datetime.datetime(2026, 11, 20, 20, 0).isoformat()}}
    f = S.for_dataset("callmoney", runs)
    assert f["last_run"].startswith("2026-11-20T20") and f["last_run_status"] == "ok"
    assert S.for_dataset("refrate", runs)["last_run"] is None           # never reported -> None, shown as 'not recorded'


def _ledger(rows):
    import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db as dbmod
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for hours_ago, machine, step, status, err in rows:
        s.add(dbmod.StepRun(run_utc=datetime.datetime(2026, 11, 21, 12) - datetime.timedelta(hours=hours_ago), machine=machine,
                            step=step, status=status, error=err))
    s.commit()
    return s


def test_githubs_blocked_run_is_not_the_headline_when_the_pc_read_the_page_fine():
    """The panel said 'Last run had 8 errors: policy fetch returned nothing' while the PC had read policy fine an hour earlier:
    GitHub is CAPTCHAd by BB's firewall, so its newest row described the backup, not the desk's data."""
    import datetime
    s = _ledger([(2, "pc", "policy", "ok", None), (0.2, "github", "policy", "failed", "returned nothing"),
                 (2, "pc", "refrate", "failed", "page not served")])
    got = S.current_problems(s, datetime.datetime(2026, 11, 21, 12))
    assert got == ["refrate: failed (pc) - page not served"]


def test_without_a_recent_pc_row_the_newest_row_of_any_machine_speaks():
    import datetime
    s = _ledger([(30, "pc", "policy", "ok", None), (1, "github", "policy", "failed", "returned nothing")])
    assert S.current_problems(s, datetime.datetime(2026, 11, 21, 12)) == ["policy: failed (github) - returned nothing"]
