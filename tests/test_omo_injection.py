"""How much is BB injecting - as a stock, measured where BB's data actually ends.

The two ways this goes wrong are the ones that look plausible: adding up what was
dealt in a window (every rolled tranche counted again) and measuring at today's
date when the last two days' operations are not yet published (the stock is then
understated). IBLF and MLS - the Islamic banks' facilities - must appear in the
injection like any other, and a facility nobody listed must still appear.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import OMOTransaction

sys.path.append(os.path.join(_ROOT, "api", "routers"))
import omo_logic as O  # noqa: E402

D = datetime.date


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _t(s, txn, mat, instr, direction, crore, rate=10.0):
    s.add(OMOTransaction(transaction_date=txn, maturity_date=mat, instrument=instr,
                         direction=direction, accepted_bdt_crore=crore, tenor_label="7D",
                         tenor_days=7, rate_pct=rate))


def _world():
    s = _mem()
    _t(s, D(2026, 10, 1), D(2026, 10, 30), "CB_REPO", "INJECTION", 1000.0, 9.0)
    _t(s, D(2026, 10, 2), D(2026, 10, 17), "IBLF", "INJECTION", 400.0, 3.0)   # live on the 16th, matured by the 18th
    _t(s, D(2026, 10, 14), D(2026, 10, 24), "IBLF", "INJECTION", 300.0, 4.0)
    _t(s, D(2026, 10, 15), D(2026, 11, 30), "MLS", "INJECTION", 200.0, 5.0)
    _t(s, D(2026, 10, 16), D(2026, 10, 17), "SDF", "ABSORPTION", 600.0, 7.5)  # matured before as_of
    _t(s, D(2026, 10, 16), D(2026, 10, 19), "SDF", "ABSORPTION", 500.0, 7.5)
    _t(s, D(2026, 10, 16), D(2026, 10, 25), "NEWFAC", "INJECTION", 100.0)      # unlisted facility
    s.commit()
    return s


def test_injection_is_the_stock_of_unmatured_tranches():
    r = O.injection_summary(_world(), as_of="2026-10-16")
    by = {v["instrument"]: v for v in r["instruments"]}
    assert by["IBLF"]["outstanding_crore"] == 700.0     # 400 + 300: both live on the 16th
    assert r["totals"]["injected_crore"] == 1000 + 700 + 200 + 100
    assert r["totals"]["absorbed_crore"] == 1100.0       # both SDFs live on the 16th


def test_a_matured_tranche_is_not_counted_after_it_matures():
    r = O.injection_summary(_world(), as_of="2026-10-18")
    by = {v["instrument"]: v for v in r["instruments"]}
    assert by["IBLF"]["outstanding_crore"] == 300.0
    assert r["totals"]["absorbed_crore"] == 500.0


def test_as_of_defaults_to_the_last_published_day_not_today():
    """Today has no data yet; measuring there would show the stock as empty."""
    r = O.injection_summary(_world())
    assert r["as_of"] == "2026-10-16" and r["totals"]["injected_crore"] > 0


def test_the_islamic_facilities_are_a_subtotal_of_the_injection():
    r = O.injection_summary(_world(), as_of="2026-10-16")
    assert r["totals"]["islamic_injected_crore"] == 700.0 + 200.0
    assert r["totals"]["conventional_injected_crore"] == 1000.0 + 100.0
    assert {v["instrument"] for v in r["instruments"] if v["islamic"]} == {"IBLF", "MLS"}


def test_a_facility_nobody_listed_still_appears():
    """A product missing from a dashboard is the failure this project exists to avoid."""
    r = O.injection_summary(_world(), as_of="2026-10-16")
    assert "NEWFAC" in {v["instrument"] for v in r["instruments"]}


def test_net_injection_is_injected_less_absorbed():
    t = O.injection_summary(_world(), as_of="2026-10-16")["totals"]
    assert t["net_injected_crore"] == round(t["injected_crore"] - t["absorbed_crore"], 2)


def test_shares_add_to_the_injected_total_and_absorption_has_none():
    r = O.injection_summary(_world(), as_of="2026-10-16")
    inj = [v for v in r["instruments"] if v["direction"] == "INJECTION"]
    assert abs(sum(v["share_of_injected_pct"] for v in inj) - 100.0) < 0.5
    assert all(v["share_of_injected_pct"] is None for v in r["instruments"]
               if v["direction"] == "ABSORPTION")


def test_rate_is_weighted_by_amount():
    r = O.injection_summary(_world(), as_of="2026-10-16")
    iblf = next(v for v in r["instruments"] if v["instrument"] == "IBLF")
    assert iblf["wtd_rate_pct"] == round((400 * 3.0 + 300 * 4.0) / 700, 4)


def test_dealt_in_the_window_sits_beside_the_stock_not_instead_of_it():
    r = O.injection_summary(_world(), as_of="2026-10-16", window_days=5)
    iblf = next(v for v in r["instruments"] if v["instrument"] == "IBLF")
    assert iblf["dealt_window_crore"] == 300.0           # only the 14-Oct deal is in the window
    assert iblf["outstanding_crore"] == 700.0


def test_maturing_soon_is_the_rolloff_after_as_of():
    r = O.injection_summary(_world(), as_of="2026-10-16")
    cb = next(v for v in r["instruments"] if v["instrument"] == "CB_REPO")
    assert cb["maturing_7d_crore"] == 0.0 and cb["maturing_30d_crore"] == 1000.0
    assert cb["next_maturity"] == "2026-10-30"


def test_string_dates_from_sqlite_are_coerced_not_dropped():
    assert O._d("2026-10-16") == D(2026, 10, 16)
    assert O._d(datetime.datetime(2026, 10, 16, 3)) == D(2026, 10, 16)


def test_no_data_is_said_not_zeroed():
    r = O.injection_summary(_mem())
    assert r["totals"] is None and r["as_of"] is None


def test_it_respects_the_api_deploy_boundary():
    import ast
    src = open(os.path.join(_ROOT, "api", "routers", "omo_logic.py"), encoding="utf-8").read()
    mods = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
            for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if hasattr(n, "names") else [])}
    assert not mods & {"db", "engines", "fetchers", "config"}


class TestTheOutstandingExport:
    """OMO outstanding by product and day, as a file the desk can hand on."""

    def _txns(self):
        D = datetime.date
        return [
            {"transaction_date": D(2026, 10, 12), "maturity_date": D(2026, 10, 19), "instrument": "IBLF",
             "accepted_bdt_crore": 7000.0, "direction": "INJECTION"},
            {"transaction_date": D(2026, 10, 13), "maturity_date": D(2026, 10, 14), "instrument": "SDF",
             "accepted_bdt_crore": 2800.0, "direction": "ABSORPTION"},
            {"transaction_date": D(2026, 10, 13), "maturity_date": D(2026, 10, 20), "instrument": "NEWFAC",
             "accepted_bdt_crore": 100.0, "direction": "INJECTION"},
        ]

    def test_outstanding_is_the_stock_and_splits_injection_from_mop_up(self):
        from omo_logic import outstanding_by_day
        D = datetime.date
        p = outstanding_by_day(self._txns(), D(2026, 10, 12), D(2026, 10, 15))
        day = {d["date"]: d for d in p["days"]}
        assert day["2026-10-13"]["injection"] == 7100.0 and day["2026-10-13"]["mop_up"] == 2800.0
        assert day["2026-10-13"]["net"] == 4300.0 and day["2026-10-13"]["total"] == 9900.0
        assert day["2026-10-14"]["mop_up"] == 0.0          # the SDF matured: the stock, not what was dealt

    def test_a_facility_the_legend_has_never_heard_of_still_gets_a_column(self):
        """A new BB product must appear the day it is dealt, never vanish from the file."""
        from omo_logic import outstanding_by_day
        D = datetime.date
        p = outstanding_by_day(self._txns(), D(2026, 10, 12), D(2026, 10, 15))
        assert "NEWFAC" in p["instruments"] and p["days"][1]["by_instrument"]["NEWFAC"] == 100.0
        assert p["instruments"].index("IBLF") < p["instruments"].index("SDF") < p["instruments"].index("NEWFAC")

    def test_the_export_is_registered_with_a_date_range(self):
        import export_registry as R
        e = R.BY_KEY["omo_outstanding"]
        assert "date_from" in e.args and e.table == "omo_transactions"
