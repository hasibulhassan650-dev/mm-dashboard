"""Server timestamps are UTC with no zone ("2026-11-18T14:49:53"). `new Date()` reads a zone-less date-time as LOCAL time, so for a user in
Bangladesh (UTC+6) every "refreshed Nh ago" was six hours too old: a refresh that finished minutes earlier read "6h ago", which can also trip the
'refresh has stopped' banner for a healthy system. Everything that shows an age or a clock time goes through parseTs."""
import os
import re

_FRONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")


def _src(*parts):
    with open(os.path.join(_FRONT, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_parse_ts_treats_a_zoneless_datetime_as_utc_and_leaves_dates_and_zones_alone():
    src = _src("lib", "format.ts")
    assert "export function parseTs" in src
    assert r'T\d{2}:\d{2}' in src and 'iso + "Z"' in src, "a date-time with no zone must be read as UTC"
    assert r"(Z|[+-]\d{2}:?\d{2})$" in src, "an explicit zone must be respected"


def test_no_age_or_clock_in_the_ui_parses_a_server_timestamp_as_local_time():
    bad = re.compile(r"new Date\((iso|s\.last_run|s\.health_as_of|r\.changed_utc)\)")
    for rel in (("lib", "format.ts"), ("components", "DataWarning.tsx"), ("components", "terminal", "UpdateStatus.tsx"),
                ("app", "guardian", "page.tsx")):
        hits = [m.group(0) for m in bad.finditer(_src(*rel)) if "parseTs" not in m.group(0)]
        assert not hits, f"{'/'.join(rel)} reads a server timestamp as local time: {hits}"


def test_the_verdict_is_computed_after_the_repairs_it_should_reflect():
    """The Doctor and the step ledger used to run AFTER the integrity verdict, so the banner kept showing a problem the Doctor had already
    fixed until the NEXT hourly run recorded a new verdict."""
    src = open(os.path.join(os.path.dirname(_FRONT), "weekly_fetch.py"), encoding="utf-8").read()
    assert src.index("run_doctor(session)") < src.index("quality = integrity_check()")
    assert src.index("record_step_runs(session") < src.index("quality = integrity_check()")
