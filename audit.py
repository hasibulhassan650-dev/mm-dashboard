"""The full sweep: every dataset, every gap, ranked.

Written because the desk asked a question nothing could answer — "I don't know
how much backlog or data inaccuracy you have" — after finding three problems
itself that the automated checks had not reported.

The daily checks answer "is anything wrong right now". This answers "what is
the complete state of every dataset", which is a different question and the one
you need before you can trust a number you did not personally verify.

Reports, per dataset:

  * coverage      earliest and latest row, and how many
  * freshness     age, and whether that is fine / waiting / a defect
  * gaps          working days missing INSIDE the range, holiday-aware
  * continuity    the longest run of consecutive missing working days

Run it directly for a report, or import `sweep()` for the structured result.
Read-only: it never writes.
"""
import argparse
import datetime
import logging
import sys
from typing import Dict, List, Optional

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from sqlalchemy import inspect, text

import calendar_utils as _cal
import engines.absences as _absences
import engines.expectations as EX
from db import get_session

log = logging.getLogger("audit")

SEV_ORDER = {"defect": 0, "waiting": 1, None: 2}


def _as_date(v) -> Optional[datetime.date]:
    if v is None or hasattr(v, "year"):
        return v
    t = str(v)[:10]
    if len(t) == 7:            # monthly series store YYYY-MM
        t += "-01"
    try:
        return datetime.date.fromisoformat(t)
    except ValueError:
        return None


def _holidays(session) -> set:
    try:
        rows = session.execute(text(
            "SELECT calendar_date FROM holiday_calendar WHERE "
            + _cal.CLOSURE_SQL)).fetchall()
        return {d for d in (_as_date(r[0]) for r in rows) if d is not None}
    except Exception:
        session.rollback()
        return set()


def sweep(session=None, today: datetime.date = None, gap_window_days: int = 120) -> dict:
    """Examine every declared dataset. Returns a structured report."""
    close = session is None
    session = session or get_session()
    today = today or datetime.date.today()
    holidays = _holidays(session)
    tables = set(inspect(session.get_bind()).get_table_names())

    findings: List[dict] = []
    datasets: List[dict] = []

    for ds in EX.DATASETS:
        row = {"key": ds.key, "label": ds.label, "table": ds.table,
               "cadence": ds.cadence, "source": ds.source}
        if ds.table not in tables:
            row["error"] = "table missing"
            datasets.append(row)
            findings.append({"severity": "defect", "dataset": ds.key,
                             "kind": "missing-table",
                             "detail": f"{ds.table} does not exist"})
            continue
        try:
            n, lo, hi = session.execute(text(
                f"SELECT COUNT(*), MIN({ds.date_column}), MAX({ds.date_column}) "
                f"FROM {ds.table}")).fetchone()
        except Exception as exc:
            session.rollback()
            row["error"] = str(exc)[:90]
            datasets.append(row)
            findings.append({"severity": "defect", "dataset": ds.key, "kind": "unreadable",
                             "detail": f"cannot read {ds.table}: {str(exc)[:70]}"})
            continue

        lo_d, hi_d = _as_date(lo), _as_date(hi)
        cal_age = (today - hi_d).days if hi_d else None
        # Daily series are measured in WORKING days: on a Saturday every one of
        # them is two to three calendar days old and perfectly healthy.
        age = ds.age(hi_d, today, holidays)
        # A forward-looking series is not "stale" when its newest row is in the
        # future; that is the horizon, which is checked by its own rule.
        sev = None if (cal_age is not None and cal_age < 0) else ds.severity_for_age(age)
        row.update({"rows": n, "earliest": str(lo_d) if lo_d else None,
                    "latest": str(hi_d) if hi_d else None, "age_days": age,
                    "age_unit": "working days" if ds.cadence == EX.WORKING_DAY else "days",
                    "calendar_age_days": cal_age,
                    "severity": sev, "fires_after": ds.stale_after_days,
                    "escalates_after": ds.tolerance_days})

        if n == 0:
            findings.append({"severity": "defect", "dataset": ds.key, "kind": "empty",
                             "detail": f"{ds.label} has no rows at all"})
        elif sev:
            findings.append({"severity": sev, "dataset": ds.key, "kind": "stale",
                             "detail": f"{ds.label} last moved {hi_d} "
                                       f"({age} {'working ' if ds.cadence == EX.WORKING_DAY else ''}"
                                       f"days ago); "
                                       f"published {ds.cadence.replace('_', ' ')} by {ds.source}"})

        # ── gaps inside the series ──────────────────────────────────────────
        if ds.gap_check and ds.cadence == EX.WORKING_DAY and n:
            since = today - datetime.timedelta(days=gap_window_days)
            try:
                rows = session.execute(text(
                    f"SELECT DISTINCT {ds.date_column} FROM {ds.table} "
                    f"WHERE {ds.date_column} >= :s"), {"s": str(since)}).fetchall()
            except Exception:
                session.rollback()
                rows = []
            # Raw SQL hands dates back as STRINGS on SQLite and as dates on
            # Postgres. Filtering on hasattr(..., "year") dropped every row on
            # SQLite, so this entire gap check was silently dead outside
            # production — and therefore untestable, which is how it stayed
            # dead. Coerce instead of discarding.
            present = {d for d in (_as_date(r[0]) for r in rows) if d is not None}
            if present:
                start = max(min(present), since)
                gaps = EX.missing_working_days(present, start, max(present), holidays)
                # Days BB itself does not publish, each established by re-reading
                # BB's own page from the start of the month. validate.py already
                # passes over these; this did not, so the two tools reported
                # different numbers of defects for the same database — the kind
                # of contradiction that makes both of them unusable.
                known = _absences.absent_dates(session, ds.key)
                skipped_known = len([g for g in gaps if g in known])
                gaps = [g for g in gaps if g not in known]
                if skipped_known:
                    row["known_absences"] = skipped_known
                row["gaps"] = len(gaps)
                row["gap_dates"] = [str(g) for g in gaps[:20]]
                if gaps:
                    runs, cur = [], [gaps[0]]
                    for g in gaps[1:]:
                        if EX.working_days_between(cur[-1], g, holidays) <= 1:
                            cur.append(g)
                        else:
                            runs.append(cur); cur = [g]
                    runs.append(cur)
                    longest = max(runs, key=len)
                    row["longest_gap"] = len(longest)
                    findings.append({
                        "severity": "defect", "dataset": ds.key, "kind": "gap",
                        "detail": f"{len(gaps)} working day(s) missing inside "
                                  f"{start}..{max(present)}; longest run {len(longest)} "
                                  f"from {longest[0]}"})
        datasets.append(row)

    # ── anything in the schema nobody declared ──────────────────────────────
    declared = {d.table for d in EX.DATASETS} | set(EX.NOT_WATCHED)
    for t in sorted(tables - declared):
        findings.append({"severity": "defect", "dataset": t, "kind": "unwatched",
                         "detail": f"{t} is neither watched nor deliberately exempt, so no "
                                   f"staleness could ever be reported for it"})

    findings.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 3), f["dataset"]))
    if close:
        session.close()
    return {
        "as_of": str(today),
        "datasets": datasets,
        "findings": findings,
        "defects": sum(1 for f in findings if f["severity"] == "defect"),
        "waiting": sum(1 for f in findings if f["severity"] == "waiting"),
    }


def render(rep: dict) -> str:
    out = [f"# Data audit — {rep['as_of']}", ""]
    out.append(f"**{rep['defects']} defect(s), {rep['waiting']} waiting** across "
               f"{len(rep['datasets'])} datasets.")
    out.append("")
    out.append("| dataset | rows | earliest | latest | age | gaps | state |")
    out.append("|---|---:|---|---|---:|---:|---|")
    for d in rep["datasets"]:
        if d.get("error"):
            out.append(f"| {d['label']} | — | — | — | — | — | ERROR {d['error']} |")
            continue
        state = {"defect": "**DEFECT**", "waiting": "waiting", None: "ok"}[d.get("severity")]
        gaps = d.get("gaps")
        out.append(f"| {d['label']} | {d['rows']:,} | {d['earliest'] or '—'} | "
                   f"{d['latest'] or '—'} | {d['age_days'] if d['age_days'] is not None else '—'} "
                   f"| {gaps if gaps is not None else '—'} | {state} |")
    out.append("")
    known = {d["label"]: d["known_absences"] for d in rep["datasets"]
             if d.get("known_absences")}
    if known:
        out.append("Days BB itself does not publish, passed over rather than counted as "
                   "gaps (each proven by re-reading BB's own page from the start of the "
                   "month): "
                   + ", ".join(f"**{k}** {v}" for k, v in sorted(known.items())) + ".")
        out.append("")
    if rep["findings"]:
        out.append("## Backlog, worst first")
        out.append("")
        for f in rep["findings"]:
            tag = "DEFECT " if f["severity"] == "defect" else "waiting"
            out.append(f"- `{tag}` **{f['dataset']}** ({f['kind']}): {f['detail']}")
    else:
        out.append("No findings. Every declared dataset is current and continuous.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Full data audit across every dataset")
    ap.add_argument("--window", type=int, default=120,
                    help="days of history to check for gaps (default 120)")
    ap.add_argument("--out", help="write the markdown report here as well")
    ap.add_argument("--fail-on-defect", action="store_true",
                    help="exit 1 when defects are found (for CI)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    rep = sweep(gap_window_days=args.window)
    text_out = render(rep)
    print(text_out)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text_out + "\n")
    if args.fail_on_defect and rep["defects"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
