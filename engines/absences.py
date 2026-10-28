"""Days Bangladesh Bank does not publish, established by looking.

The gap check calls a missing working day inside a series our defect. That is
right almost always — interbank_repo was missing 27-30 Sep because our fetcher
only ever read the last five trading days, and OMO was missing ten days because
BB renamed a CSS class. Both were ours.

But not always. On Thursday 08-Oct-2026 BB's interbank FX turnover table had no
row, while call money, repo, OMO and reference rates all printed normally; the
page, re-read a week later over its full two-month window, still has no 08-Oct.
No fetch will ever fill that day. Left as a defect it is one permanently red
line in the report, and a check that is always red is a check nobody reads —
the exact failure that let an eleven-day outage hide in plain sight.

So an absence can be recorded, and the gap check then passes over it. Two rules
keep that from becoming a mute button:

  1. EVIDENCE, NOT ASSERTION. An absence is recorded only from a successful
     fetch whose window straddles the date and came back without it. A fetch
     that failed, or that returned nothing, records nothing — those days stay
     defects, because "we could not look" and "BB has nothing" are different
     facts and only one of them is ours.

  2. VISIBLE FOREVER. Every absence keeps the reason, the page it was read
     from, when it was verified and how many times it has been re-confirmed.
     The digest prints the count. A skipped day stays a known hole in the
     record rather than becoming invisible.
"""
import datetime
import logging
from typing import Iterable, List, Optional, Set

log = logging.getLogger("absences")


def _as_date(v) -> Optional[datetime.date]:
    if v is None or hasattr(v, "year"):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def absent_dates(session, dataset: str) -> Set[datetime.date]:
    """Dates already established as BB's own omissions for this dataset."""
    from sqlalchemy import text
    try:
        rows = session.execute(text(
            "SELECT absent_date FROM data_absences WHERE dataset = :d"),
            {"d": dataset}).fetchall()
    except Exception:
        session.rollback()          # table not migrated yet
        return set()
    return {d for d in (_as_date(r[0]) for r in rows) if d is not None}


def _anchored_windows(got: List[datetime.date],
                      holidays: Set[datetime.date]) -> List[tuple]:
    """The months this fetch returned FROM THEIR START, as (first, last) pairs.

    This is the rule that makes an absence credible, and it was learned the
    hard way. Asking BB for the CURRENT month through its default page returns
    a rolling tail — on 15-Oct-2026 the interbank FX page gave 10-14 Oct and
    nothing earlier, though BB had published 01-08 Oct. A window like that
    proves nothing whatever about the days it omits, and reading it as evidence
    recorded five absences that were not absences.

    What matters is the ANCHOR, not completeness. A window that begins at or
    before the month's first working day has cut nothing off ahead of the date
    in question, so a working day missing from inside it is missing from BB.
    Requiring the month to be finished as well was stricter than the purpose
    needed: BB's explicit month query for October returned 01-Oct onward on
    17-Oct, which settles 08-Oct beyond doubt while October still has a
    fortnight to run.

    The upper bound is still the newest date fetched, and candidates must sit
    STRICTLY inside the window — so BB's publication lag at the newest edge can
    never be mistaken for an omission.
    """
    import engines.expectations as EX
    out = []
    months = sorted({(d.year, d.month) for d in got})
    for y, m in months:
        last_dom = (datetime.date(y + (m == 12), (m % 12) + 1, 1)
                    - datetime.timedelta(days=1))
        first_wd = next((datetime.date(y, m, 1) + datetime.timedelta(days=i)
                         for i in range(0, 10)
                         if EX.is_working_day(datetime.date(y, m, 1)
                                              + datetime.timedelta(days=i), holidays)), None)
        last_wd = next((last_dom - datetime.timedelta(days=i)
                        for i in range(0, 10)
                        if EX.is_working_day(last_dom - datetime.timedelta(days=i), holidays)),
                       None)
        if first_wd is None or last_wd is None:
            continue
        in_month = [d for d in got if (d.year, d.month) == (y, m)]
        if in_month and min(in_month) <= first_wd:
            # Judge up to the newest date this month actually returned, not to
            # the month's end: a month still running has days BB has not
            # reached, and they are not absences.
            out.append((first_wd, min(max(in_month), last_wd)))
    return out


def lost_dates(session, dataset: str) -> Set[datetime.date]:
    """The subset of known holes that are OURS — days we had and destroyed."""
    from sqlalchemy import text
    try:
        rows = session.execute(text(
            "SELECT absent_date FROM data_absences WHERE dataset = :d "
            "AND kind = 'lost'"), {"d": dataset}).fetchall()
    except Exception:
        session.rollback()
        return set()
    return {d for d in (_as_date(r[0]) for r in rows) if d is not None}


def confirm_absences(session, dataset: str, fetched_dates: Iterable,
                     holidays: Set[datetime.date], source: str = "",
                     reason: str = "", now: datetime.datetime = None,
                     stored_dates: Optional[Set[datetime.date]] = None
                     ) -> List[datetime.date]:
    """Record the working days BB's own page did not carry.

    Two conditions, both required:

      * the date sits inside a calendar month this fetch returned IN FULL (not
        a rolling tail — see `_anchored_windows`);
      * we hold no row for it either. A day we already have is not a gap, and
        a day we have that BB no longer shows is evidence about BB's archive,
        never about what BB published.
    """
    import engines.expectations as EX
    from db import DataAbsence
    now = now or datetime.datetime.utcnow()
    got = sorted({d for d in (_as_date(x) for x in fetched_dates) if d is not None})
    if len(got) < 3:
        # Too little to establish a window. Silence here is correct: a fetch
        # that returned almost nothing is a candidate defect, not a source of
        # proof about what BB publishes.
        return []

    windows = _anchored_windows(got, holidays)
    if not windows:
        return []
    have = set(stored_dates) if stored_dates is not None else _stored_dates(session, dataset)

    missing = []
    for lo, hi in windows:
        missing += EX.missing_working_days(set(got) | have, lo, hi, holidays)

    recorded: List[datetime.date] = []
    for d in missing:
        row = (session.query(DataAbsence)
               .filter_by(dataset=dataset, absent_date=d).one_or_none())
        if row is None:
            session.add(DataAbsence(
                dataset=dataset, absent_date=d, source=source, verified_utc=now,
                checks=1,
                reason=reason or (f"BB's page, re-read from the start of "
                                  f"{', '.join(f'{a}..{b}' for a, b in windows)}, "
                                  f"carries no row for this working day and nor "
                                  f"do we")))
            recorded.append(d)
            log.warning("%s: %s confirmed absent at the source", dataset, d)
        else:
            row.checks = (row.checks or 0) + 1
            row.verified_utc = now
    return recorded


def _stored_dates(session, dataset: str) -> Set[datetime.date]:
    """What we already hold for this dataset — a day we have is not a gap."""
    from sqlalchemy import text
    import engines.expectations as EX
    ds = EX.BY_KEY.get(dataset)
    if ds is None:
        return set()
    try:
        rows = session.execute(text(
            f"SELECT DISTINCT {ds.date_column} FROM {ds.table}")).fetchall()
    except Exception:
        session.rollback()
        return set()
    return {d for d in (_as_date(r[0]) for r in rows) if d is not None}


def record_loss(session, dataset: str, day: datetime.date, reason: str,
                source: str = "", now: datetime.datetime = None) -> bool:
    """Record a day we HAD and destroyed, and that BB cannot give back.

    Not the same thing as an absence, and the digest must never blur them: a
    BB omission is a hole in what was published, a loss is a hole in our own
    record. 15-Oct-2026's exchange-rate row is the case: a mis-dated row
    overwrote it, and BB's page serves only today's snapshot, so it cannot be
    re-read. Leaving it a defect would put one permanently red line in every
    report for 45 days — wallpaper — and calling it BB's omission would be a
    lie. So it is kept, named as OURS, and surfaced in its own line.

    Returns True if newly recorded.
    """
    from db import DataAbsence
    now = now or datetime.datetime.utcnow()
    row = (session.query(DataAbsence)
           .filter_by(dataset=dataset, absent_date=day).one_or_none())
    if row is not None:
        row.checks = (row.checks or 0) + 1
        row.verified_utc = now
        return False
    session.add(DataAbsence(dataset=dataset, absent_date=day, kind="lost", reason=reason,
                            source=source, verified_utc=now, checks=1))
    return True


KNOWN_ABSENCES_PATH = None      # set below: data/seeds/known_absences.yaml


def sync_known_absences(session, path: str = None, now: datetime.datetime = None) -> dict:
    """Record the holes a person has established from evidence the fetchers cannot gather themselves.

    `confirm_absences` proves an absence from a page re-read over a whole month. Some holes cannot be
    proven that way - an OMO release BB never posted (its press list carries the days either side and
    the serial number skips), an exchange-rate day lost while BB's firewall blocked our runners and
    the page only ever shows today. They are written down in data/seeds/known_absences.yaml with the
    evidence, and loaded here under the same two rules as every other absence: a reason must be given,
    and a day we HOLD data for is never recorded as absent. Returns {"recorded", "errors"}."""
    import os

    import yaml

    import engines.expectations as EX
    from db import DataAbsence
    path = path or KNOWN_ABSENCES_PATH
    now = now or datetime.datetime.utcnow()
    out = {"recorded": [], "errors": []}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    for i, e in enumerate(doc.get("absences") or [], 1):
        where = f"known_absences.yaml entry {i}"
        ds, day, kind = e.get("dataset"), _as_date(e.get("date")), e.get("kind") or "bb_omission"
        if ds not in EX.BY_KEY or day is None or kind not in ("bb_omission", "lost") or not (e.get("reason") or "").strip():
            out["errors"].append(f"{where}: needs a known dataset, a date, kind bb_omission|lost and a reason")
            continue
        if kind == "bb_omission" and day in _stored_dates(session, ds):
            out["errors"].append(f"{where}: we hold {ds} data for {day}, so it is not an absence")
            continue
        row = session.query(DataAbsence).filter_by(dataset=ds, absent_date=day).one_or_none()
        if row is None:
            session.add(DataAbsence(dataset=ds, absent_date=day, kind=kind, reason=e["reason"].strip(),
                                    source=(e.get("source") or "")[:300], verified_utc=now, checks=1))
            out["recorded"].append(f"{ds} {day} ({kind})")
        else:
            row.checks = (row.checks or 0) + 1
            row.verified_utc = now
    return out


def summary(session, kind: Optional[str] = "bb_omission") -> dict:
    """What is on record, for the digest. Never a bare count: the newest one
    matters, because a cluster of fresh absences is a story about BB."""
    from sqlalchemy import text
    try:
        # COALESCE: rows written before the column existed are BB omissions.
        # Conditional SQL rather than `:k IS NULL`: an untyped NULL parameter
        # fails type inference on Postgres, which SQLite never reveals.
        where = "WHERE COALESCE(kind, 'bb_omission') = :k " if kind else ""
        rows = session.execute(text(
            "SELECT dataset, COUNT(*), MAX(absent_date) FROM data_absences "
            + where + "GROUP BY dataset ORDER BY dataset"),
            {"k": kind} if kind else {}).fetchall()
    except Exception:
        session.rollback()
        return {}
    return {r[0]: {"count": r[1], "latest": str(r[2])} for r in rows}


import os as _os
KNOWN_ABSENCES_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                    "data", "seeds", "known_absences.yaml")
