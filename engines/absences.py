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


def known_print_errors(path: str = None) -> list:
    """BB maturities its own releases contradict, from known_absences.yaml `print_errors`.

    Each is {dataset, instrument, date, omitted_crore, reason}. An entry without a reason, an amount or a
    date is dropped (an exception nobody can audit is not an exception). Read by validate.py's omo-ledger
    check, which still reports the day - as a notice - and only while the gap equals the omitted amount."""
    import os

    import yaml
    path = path or KNOWN_ABSENCES_PATH
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out = []
    for e in doc.get("print_errors") or []:
        day = _as_date(e.get("date"))
        try:
            amt = float(e.get("omitted_crore"))
        except (TypeError, ValueError):
            continue
        if day and e.get("instrument") and amt > 0 and (e.get("reason") or "").strip():
            out.append({"dataset": e.get("dataset") or "omo", "instrument": e["instrument"], "date": day,
                        "omitted_crore": amt, "reason": e["reason"].strip()})
    return out


def known_tenor_corrections(path: str = None, session=None) -> list:
    """Tranches whose printed tenor BB's OWN later maturity print contradicts, from known_absences.yaml
    `tenor_corrections`: {dataset, instrument, deal_date, accepted_crore, true_maturity, reason}.

    The proof is arithmetic on BB's figures, never a guess: the 25-Oct "CB Repo 7-Days 15,861.75" is printed as
    maturing 15,870.00 on 27-Oct, which is principal plus exactly 2 days' profit at 9.50%. An entry without a
    reason, a date or an amount, or whose maturity is not after the deal, is dropped."""
    import os

    import yaml
    path = path or KNOWN_ABSENCES_PATH
    doc = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    out = []
    # corrections the Doctor proved from BB's own maturity prints (db.TenorOverride): consulted exactly like the YAML ones
    if session is not None:
        try:
            from db import TenorOverride
            for o in session.query(TenorOverride).all():
                out.append({"dataset": "omo", "instrument": o.instrument, "deal_date": o.deal_date,
                            "accepted_crore": o.accepted_crore, "true_maturity": o.true_maturity,
                            "reason": "proved from BB's own later maturity print (Doctor): " + (o.evidence or "")})
        except Exception:
            session.rollback()
    for e in doc.get("tenor_corrections") or []:
        deal, mat = _as_date(e.get("deal_date")), _as_date(e.get("true_maturity"))
        try:
            amt = float(e.get("accepted_crore"))
        except (TypeError, ValueError):
            continue
        if deal and mat and mat > deal and e.get("instrument") and amt > 0 and (e.get("reason") or "").strip():
            out.append({"dataset": e.get("dataset") or "omo", "instrument": e["instrument"], "deal_date": deal,
                        "accepted_crore": amt, "true_maturity": mat, "reason": e["reason"].strip()})
    return out


def apply_tenor_correction(txn: dict, corrections: list) -> bool:
    """Correct an incoming parsed row in place (so a re-read of BB's PDF cannot resurrect the printed tenor).
    The printed tenor_label stays as BB wrote it; tenor_days and maturity_date become what BB's own maturity
    print proves, because the profit, the outstanding and the ladder all key on them."""
    for c in corrections:
        if (txn.get("instrument") == c["instrument"] and txn.get("transaction_date") == c["deal_date"]
                and abs((txn.get("accepted_bdt_crore") or 0) - c["accepted_crore"]) < 0.01):
            days = (c["true_maturity"] - c["deal_date"]).days
            if txn.get("maturity_date") == c["true_maturity"] and txn.get("tenor_days") == days:
                return False
            txn["maturity_date"], txn["tenor_days"] = c["true_maturity"], days
            return True
    return False


def sync_tenor_corrections(session, path: str = None) -> dict:
    """Apply the corrections to rows already stored. Returns {"corrected": [...]}; safe to run every time."""
    from db import OMOTransaction
    out = {"corrected": []}
    for c in known_tenor_corrections(path, session):
        days = (c["true_maturity"] - c["deal_date"]).days
        for row in session.query(OMOTransaction).filter(
                OMOTransaction.transaction_date == c["deal_date"], OMOTransaction.instrument == c["instrument"]).all():
            if abs((row.accepted_bdt_crore or 0) - c["accepted_crore"]) >= 0.01:
                continue
            if row.maturity_date != c["true_maturity"] or row.tenor_days != days:
                row.maturity_date, row.tenor_days = c["true_maturity"], days
                out["corrected"].append(f"{c['instrument']} {c['deal_date']} -> matures {c['true_maturity']}")
    return out


LEDGER_TOL_PCT, LEDGER_TOL_ABS = 0.015, 10.0


def grown_principal(accepted, rate_pct, tenor_days, deal_date=None, maturity_date=None) -> float:
    """What BB pays back on a tranche: principal plus profit for the days it actually ran.

    BB pays profit for the ELAPSED days, and a maturity rolled over a weekend or closure runs longer than its
    tenor: the 3-May AR "180D" matured 1-Nov, 182 days later, and BB's 1-Nov print only reconciles to the paisa
    with 182 days. Growing by the label (180) leaves a 1.13 cr hole that was being read as a missing deal."""
    days = int(tenor_days or 0)
    d, m = _as_date(deal_date), _as_date(maturity_date)
    if d and m and m > d:
        days = (m - d).days
    return float(accepted or 0) * (1 + float(rate_pct or 0) / 100 * days / 365)


def absence_maturity_day(absent_day, tenor_days: int, holidays=None) -> datetime.date:
    """The day a deal struck on a day BB never published would mature: deal date + tenor, rolled to the next
    working day exactly as every stored maturity is (calendar_utils.roll_date). Without the roll a 14-day deal of
    Saturday 17-Oct lands on Saturday 31-Oct, a day nothing matures, and the 1-Nov shortfall goes unexplained."""
    import calendar_utils
    return calendar_utils.roll_date(absent_day + datetime.timedelta(days=int(tenor_days)), holidays)[0]


def known_ledger_exceptions(path: str = None) -> list:
    """Per-tenor ledger gaps a person has proven or labelled, from known_absences.yaml `ledger_exceptions`:
    {dataset, instrument, date, tenor_days, gap_crore, tolerance_crore, status, reason}.

    status `lost_deal` = a deal of OURS that BB's own figures prove existed and that we lost (arithmetic in the
    reason); status `inferred_deal` = a deal whose existence is INFERRED, not proven (a round principal at the rate then in
    force reproduces BB's printed amount to the paisa, on a tenor and date that fit, but no second BB statement confirms
    it, and the release that would is not re-readable here); status `unexplained` = a BB maturity no deal in our record explains. Neither is a mute button: an
    entry excuses ONLY the tenor row whose gap still equals gap_crore (within tolerance_crore) - another amount on
    that day, or a BB correction, is judged normally. An entry lacking a reason, date, tenor or amount is dropped."""
    import os

    import yaml
    path = path or KNOWN_ABSENCES_PATH
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out = []
    for e in doc.get("ledger_exceptions") or []:
        day = _as_date(e.get("date"))
        try:
            tenor, gap = int(e.get("tenor_days")), float(e.get("gap_crore"))
            tol = float(e.get("tolerance_crore", 1.0))
        except (TypeError, ValueError):
            continue
        if (day and e.get("instrument") and gap > 0 and tenor > 0 and e.get("status") in ("lost_deal", "inferred_deal", "unexplained")
                and (e.get("reason") or "").strip()):
            out.append({"dataset": e.get("dataset") or "omo", "instrument": e["instrument"], "date": day,
                        "tenor_days": tenor, "gap_crore": gap, "tolerance_crore": tol,
                        "status": e["status"], "reason": " ".join(str(e["reason"]).split())})
    return out


def judge_ledger_day(inst: str, day: datetime.date, printed: dict, ours: dict, absent_dates,
                     exceptions=(), holidays=None) -> tuple:
    """One instrument, one day: do the tranches we hold maturing on `day` match what BB printed?

    `printed` and `ours` are {tenor_days: crore} (BB's figure is principal + profit; ours is grown with
    `grown_principal`). Returns (ok, notes, excused): `notes` = [(severity, text)] for every shortfall that was
    excused and WHY (severity "known" or "unexplained"), so the caller reports them - excusing is never silent.

    A shortfall is excused only by proof that fits it exactly, never by "some absence is near":
      * a recorded ledger exception whose tenor row still has the recorded gap;
      * a recorded absent deal day `a` whose `a + tenor`, rolled to a working day, IS `day`, for the tenor of
        BB's maturity row - and only up to that row's own shortfall. Tranches of ours at a tenor BB did not print
        (BB printed the 2-day CB Repo under 7D) first cover those shortfalls, so a mislabelled print cannot let an
        absence swallow a real hole elsewhere.
    What is excused is then taken off BB's figure and the remainder must still reconcile, so a surplus (a phantom
    tranche) is never hidden behind an excused shortfall."""
    bb, mine = sum(printed.values()), sum(ours.values())
    tol = max(LEDGER_TOL_PCT * bb, LEDGER_TOL_ABS)
    if abs(mine - bb) <= tol:
        return True, [], 0.0
    if mine > bb:
        return False, [], 0.0
    short = {t: v - ours.get(t, 0.0) for t, v in printed.items() if v - ours.get(t, 0.0) > 0}
    notes, excused, handled = [], 0.0, set()
    for e in exceptions:
        t = e["tenor_days"]
        if e["instrument"] != inst or e["date"] != day or t not in short or t in handled:
            continue
        if abs(short[t] - e["gap_crore"]) <= e["tolerance_crore"]:
            excused += short[t]
            handled.add(t)
            tag = ("UNEXPLAINED" if e["status"] == "unexplained" else
                   "INFERRED (a deal we hold no record of; the arithmetic fits to the paisa, not independently confirmed)"
                   if e["status"] == "inferred_deal" else "KNOWN (lost deal, proved from BB's own figures)")
            notes.append(("unexplained" if e["status"] == "unexplained" else "known",
                          f"{day} {inst} {t}D: BB printed {short[t]:,.2f} cr more than our tranches - {tag}: {e['reason']}"))
    credit = sum(v for t, v in ours.items() if t not in printed)
    explained = {}
    for t, g in short.items():
        if t in handled:
            continue
        for a in sorted(_as_date(x) for x in absent_dates):
            if a and absence_maturity_day(a, t, holidays) == day:
                explained[t] = (g, a)
                break
    # Unmatched tranches of ours first cover the shortfalls NO absence explains (the mislabelled print's own row);
    # only what is left over can eat into an absence-explained shortfall.
    unexplained_short = sum(g for t, g in short.items() if t not in handled and t not in explained)
    credit = max(0.0, credit - unexplained_short)
    cap = max(0.0, sum(g for g, _ in explained.values()) - credit)
    if cap > 0:
        excused += cap
        for t, (g, a) in sorted(explained.items()):
            notes.append(("known", f"{day} {inst} {t}D: shortfall {g:,.2f} cr is what a {t}-day deal struck on {a} (a day BB "
                                   f"never published, on record) would mature on {day} after the working-day roll - BB's "
                                   f"omission, amount not independently provable; excused up to the gap only"))
    return abs(mine - (bb - excused)) <= tol, notes, excused


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
