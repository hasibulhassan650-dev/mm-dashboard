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
