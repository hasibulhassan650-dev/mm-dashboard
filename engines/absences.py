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


def _complete_months(got: List[datetime.date],
                     holidays: Set[datetime.date]) -> List[tuple]:
    """The calendar months this fetch returned IN FULL, as (first, last) pairs.

    This is the rule that makes an absence credible, and it was learned the
    hard way. Asking BB for the CURRENT month returns a rolling tail — on
    15-Oct-2026 its interbank FX page gave 10-14 Oct and nothing earlier,
    though it had published 01-08 Oct and we hold those rows. A window like
    that proves nothing about the days it omits. A PAST month comes back whole,
    first working day to last, and a working day missing from a whole month is
    then real evidence.
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
        if in_month and min(in_month) <= first_wd and max(in_month) >= last_wd:
            out.append((first_wd, last_wd))
    return out


def confirm_absences(session, dataset: str, fetched_dates: Iterable,
                     holidays: Set[datetime.date], source: str = "",
                     reason: str = "", now: datetime.datetime = None,
                     stored_dates: Optional[Set[datetime.date]] = None
                     ) -> List[datetime.date]:
    """Record the working days BB's own page did not carry.

    Two conditions, both required:

      * the date sits inside a calendar month this fetch returned IN FULL (not
        the current month's rolling tail — see `_complete_months`);
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

    windows = _complete_months(got, holidays)
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
                reason=reason or (f"BB's page, re-read over the complete month(s) "
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


def summary(session) -> dict:
    """What is on record, for the digest. Never a bare count: the newest one
    matters, because a cluster of fresh absences is a story about BB."""
    from sqlalchemy import text
    try:
        rows = session.execute(text(
            "SELECT dataset, COUNT(*), MAX(absent_date) FROM data_absences "
            "GROUP BY dataset ORDER BY dataset")).fetchall()
    except Exception:
        session.rollback()
        return {}
    return {r[0]: {"count": r[1], "latest": str(r[2])} for r in rows}
