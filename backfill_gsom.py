"""Recover a missing GSOM settlement date from BB's own date picker.

The secondary-market table (mtm_snapshots) is captured one day at a time from
GSOM's live pages, so a day the fetch missed is a hole in the record: no market
yield, no price, no outstanding for any ISIN on that date. Three such days
survived into Oct-2026 — 30-Jun, 02-Jul and 27-Sep — and on every one of them
call money, reference rates and OMO all printed normally, so they were working
days the market traded and we simply failed to read.

They are recoverable: GSOM's pages take a `picker_date`, which is already used
to walk backwards when today's table is empty. This asks for an exact date.

The guard that matters: the settlement date is read from the PAGE's own
heading, never from what was requested. If BB serves a different date than the
one asked for — which is what it does for a date it has no table for — the
rows are REFUSED rather than stored under the requested date. Storing today's
prices under 30-Jun would not be a gap any more; it would be a fabrication, and
the gap is much the better of the two.

    python backfill_gsom.py 2026-06-30 2026-07-02 2026-09-27
    python backfill_gsom.py --missing          # find them, then fetch them
"""
import argparse
import datetime
import logging
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from sqlalchemy import text

import calendar_utils as cal
import engines.expectations as EX
from config import GSOM_FRTB_URL, GSOM_TBILL_URL, GSOM_TBOND_URL
from db import get_session, init_db
from engines.pipeline import _upsert_mtm_snapshot
from config import REQUEST_TIMEOUT
from fetchers.http_client import get_session as http_session
from parsers.securities import parse_tbill, parse_tbond_or_frtb, validate_rows

log = logging.getLogger("backfill_gsom")

PAGES = (("T_BOND", GSOM_TBOND_URL, False),
         ("FRTB", GSOM_FRTB_URL, False),
         ("T_BILL", GSOM_TBILL_URL, True))


def missing_dates(session, since: datetime.date = None) -> list:
    """Working days inside the stored range with no GSOM snapshot at all."""
    since = since or (datetime.date.today() - datetime.timedelta(days=180))
    hol = {r[0] for r in session.execute(text(
        "SELECT calendar_date FROM holiday_calendar WHERE " + cal.CLOSURE_SQL)).fetchall()
        if hasattr(r[0], "year")}
    present = {r[0] for r in session.execute(text(
        "SELECT DISTINCT settlement_date FROM mtm_snapshots WHERE settlement_date >= :s"),
        {"s": str(since)}).fetchall() if hasattr(r[0], "year")}
    if not present:
        return []
    return EX.missing_working_days(present, max(min(present), since), max(present), hol)


def fetch_one(url: str, when: datetime.date) -> str:
    """POST BB's date picker for exactly this date."""
    picker = when.strftime("%d-%b-%y").upper()        # 30-JUN-26
    s = http_session()
    s.get(url, timeout=REQUEST_TIMEOUT)               # pick up the CSRF cookie
    resp = s.post(url, data={"picker_date": picker, "ci_csrf_token": ""},
                  timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def recover(session, when: datetime.date) -> dict:
    """Fetch, verify the page really is for `when`, then store."""
    stored, refused = 0, []
    for stype, url, is_bill in PAGES:
        try:
            html = fetch_one(url, when)
        except Exception as exc:
            refused.append(f"{stype}: fetch failed ({type(exc).__name__} {exc})")
            continue
        try:
            rows = validate_rows(parse_tbill(html, url) if is_bill
                                 else parse_tbond_or_frtb(html, url, stype))
        except Exception as exc:
            refused.append(f"{stype}: parse failed ({exc})")
            continue
        if not rows:
            refused.append(f"{stype}: BB served no rows for {when}")
            continue
        # THE GUARD. The date comes from the page's heading; if BB answered
        # with a different settlement date it has nothing for the one asked
        # for, and storing those rows under `when` would invent a day of
        # prices that never existed.
        got = {r.get("settlement_date") for r in rows}
        if got != {when}:
            refused.append(f"{stype}: asked for {when}, BB's page is for "
                           f"{sorted(str(g) for g in got)} — refused")
            continue
        for r in rows:
            _upsert_mtm_snapshot(session, r)
        stored += len(rows)
    if stored:
        session.commit()
    return {"date": str(when), "stored": stored, "refused": refused}


def main():
    ap = argparse.ArgumentParser(description="Recover missing GSOM settlement dates")
    ap.add_argument("dates", nargs="*", help="YYYY-MM-DD, one or more")
    ap.add_argument("--missing", action="store_true",
                    help="work on every working day missing from the stored range")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    init_db()
    s = get_session()
    try:
        want = [datetime.date.fromisoformat(d) for d in a.dates]
        if a.missing:
            want += [d for d in missing_dates(s) if d not in want]
        if not want:
            print("Nothing to do: no dates given and none missing.")
            return
        print(f"{len(want)} date(s) to recover: {', '.join(str(d) for d in want)}\n")
        total = 0
        for d in sorted(want):
            rep = recover(s, d)
            total += rep["stored"]
            print(f"{rep['date']}: {rep['stored']} row(s) stored"
                  + (f" | refused: {'; '.join(rep['refused'])}" if rep["refused"] else ""))
        print(f"\n{total} row(s) stored in all.")
        left = missing_dates(s)
        print(f"{len(left)} working day(s) still missing"
              + (f": {', '.join(str(d) for d in left)}" if left else ""))
    finally:
        s.close()


if __name__ == "__main__":
    main()
