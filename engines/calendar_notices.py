"""
engines/calendar_notices.py - closures announced by BB circulars, into the calendar, with proof.

Why: the 20-22 Oct 2026 Puja closure was announced by a circular, the gazetted list carried only
20 and 21, and so 22 Oct looked like a missing working day. Every maturity that fell on it stayed
on a day the market was shut, and the OMO ledger reported injections as "missing" that BB had
simply settled on 25 Oct.

The rule is the project's rule: never guess. A circular's dates reach holiday_calendar only when
something other than the circular agrees with them:

  * a date that has PASSED must show a silent market (no call money, reference rate or OMO print)
    while something was observed after it - and a date on which the market TRADED rejects the
    notice, whatever the circular says;
  * a date still to come needs the government's own declaration of a general holiday, or to sit in a
    run of days the gazette already closes (the circular extends a known closure).

Everything else - an unreadable month, an unknown connector between the numbers, a reopening day
that could be mistaken for a closure - is PENDING: announced, listed as a defect, and settled by a
human in data/seeds/calendar_decisions.yaml. Reading BB's page is never evidence about the
calendar: a CAPTCHA or maintenance page changes nothing.

The Bengali PDFs use a legacy font, so the digits are reliable and the letters are not (the month
October comes out as "অনটাফয"). The month words seen are listed; an unlisted word makes the date
unreadable and the notice PENDING.
"""
import datetime
import json
import logging
import os
import re
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

WINDOW_BACK_DAYS = 45          # a closure older than this is recorded, never applied (history is not rewritten)
WINDOW_FORWARD_DAYS = 240
DECISIONS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "seeds", "calendar_decisions.yaml")

_BN = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# Month words as they come out of BB's PDFs: the proper Bengali spelling, the English one, and the
# garbled spellings actually seen. An unlisted word is unreadable, not guessed.
MONTHS: Dict[str, int] = {}
for _n, _words in {
    1: ("জানুয়ারি", "জানুয়ারী", "january", "jan"), 2: ("ফেব্রুয়ারি", "ফেব্রুয়ারী", "february", "feb"),
    3: ("মার্চ", "march", "mar"), 4: ("এপ্রিল", "april", "apr"), 5: ("মে", "may"),
    6: ("জুন", "june", "jun"), 7: ("জুলাই", "জুরাই", "july", "jul"),
    8: ("আগস্ট", "আগষ্ট", "august", "aug"), 9: ("সেপ্টেম্বর", "september", "sep", "sept"),
    10: ("অক্টোবর", "অনটাফয", "october", "oct"), 11: ("নভেম্বর", "ননবম্বয", "november", "nov"),
    12: ("ডিসেম্বর", "december", "dec"),
}.items():
    for _w in _words:
        MONTHS[_w] = _n

# What sits between two day numbers: "20 to 22" (a run of days) vs "23 and 24" (a list).
RANGE_CONN = {"হতে", "থেকে", "হইতে", "঴নত", "-", "–", "—", "to", "till", "until", "through"}
LIST_CONN = {",", "ও", "এবং", "and", "&"}
GENERAL_HOLIDAY = re.compile(r"general holiday", re.I)


class Segment:
    """One date expression found in a circular: "20 to 22 October 2026", "23 and 24 October 2026"."""

    def __init__(self, kind, dates, quote, ambiguous=None):
        self.kind, self.dates, self.quote, self.ambiguous = kind, dates, quote, ambiguous

    def __repr__(self):
        return f"Segment({self.kind}, {[str(d) for d in self.dates]}, ambiguous={self.ambiguous!r})"


def extract_segments(text: str, today: datetime.date) -> List[Segment]:
    """Every "<day>[ <connector> <day>]... <month> <year>" in the text.

    Only years near today matter (the circular cites the 2019 one it amends and the Bangla
    calendar's 1433); any other year is skipped. A number the pattern cannot account for, an unknown
    connector or an unlisted month word makes the segment ambiguous - never a guess."""
    text = (text or "").translate(_BN)
    toks = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    out: List[Segment] = []
    for i, (tok, _s, e_end) in enumerate(toks):
        tok = tok.strip(",.;:()।")                      # "2026," at the end of a clause is still a year
        if not re.fullmatch(r"20\d\d", tok) or i < 2:
            continue
        year = int(tok)
        if abs(year - today.year) > 1:
            continue
        mon_tok = toks[i - 1][0].strip(",.;:()").lower()
        if re.fullmatch(r"\d{1,2}[,،]?", toks[i - 1][0]):
            continue                                   # "... 22 2026": no month word
        nums, conns, j = [], [], i - 2
        while j >= 0 and re.fullmatch(r"\d{1,2}[,،]?", toks[j][0]):
            nums.append((int(toks[j][0].strip(",،")), toks[j][0].endswith((",", "،"))))
            j -= 1
            # a connector word followed by another number continues the expression
            if j >= 1 and re.fullmatch(r"\d{1,2}[,،]?", toks[j - 1][0]) and not re.fullmatch(r"\d", toks[j][0]) \
                    and len(toks[j][0]) <= 6 and not re.search(r"\d", toks[j][0]):
                conns.append(toks[j][0])
                j -= 1
        if not nums:
            continue
        nums.reverse(); conns.reverse()
        start = toks[j + 1][1]
        quote = text[max(0, start - 60):e_end + 60].replace("\n", " ")
        month = MONTHS.get(mon_tok)
        if month is None:
            out.append(Segment("unreadable", [], quote,
                               f"month word {toks[i - 1][0]!r} is not one of the spellings seen in BB's PDFs"))
            continue
        days = [n for n, _ in nums]
        # connectors between consecutive numbers; a comma glued to a number is a list separator
        kinds = []
        for k in range(len(days) - 1):
            c = conns[k] if k < len(conns) else None
            if c is None:
                kinds.append("list" if nums[k][1] else "?")
            elif c in RANGE_CONN:
                kinds.append("range")
            elif c in LIST_CONN:
                kinds.append("list")
            else:
                kinds.append("?")
        try:
            if "?" in kinds:
                out.append(Segment("unreadable", [], quote, f"connector between the day numbers is not recognised "
                                                            f"({conns or 'none'})"))
                continue
            if len(days) == 1:
                out.append(Segment("single", [datetime.date(year, month, days[0])], quote))
            elif set(kinds) == {"range"} and len(days) == 2:
                a, b = days
                if b < a:
                    out.append(Segment("unreadable", [], quote, "a run of days that ends before it starts"))
                else:
                    out.append(Segment("range", [datetime.date(year, month, d) for d in range(a, b + 1)], quote))
            elif set(kinds) == {"list"}:
                out.append(Segment("list", [datetime.date(year, month, d) for d in days], quote))
            else:
                out.append(Segment("unreadable", [], quote, "a mix of runs and lists in one expression"))
        except ValueError:
            out.append(Segment("unreadable", [], quote, "a day number that is not a date in that month"))
    return out


def assess(title: str, segments: List[Segment], today: datetime.date, *, is_workday: Callable,
           is_closed: Callable, activity: Callable, last_observed: Optional[datetime.date],
           published: Optional[datetime.date] = None) -> Dict:
    """What should happen to this circular. Pure: every fact about the world comes in as an argument.

    Returns {"status", "dates", "reason", "quote"} where dates are the NEW closures to write."""
    general = bool(GENERAL_HOLIDAY.search(title or ""))
    unreadable = [s for s in segments if s.kind == "unreadable"]
    # A general holiday is declared for a day AFTER the letter: the letter's own date and the government
    # memo it cites (both single dates in the text) are references, not closures.
    closing = [s for s in segments if s.kind in ("range", "list")
               or (s.kind == "single" and general and (published is None or s.dates[0] > published))]
    quote = (closing[0].quote if closing else (unreadable[0].quote if unreadable else ""))[:400]

    if unreadable and published and published < today - datetime.timedelta(days=WINDOW_BACK_DAYS):
        # months old and unreadable: nothing left to protect, and a permanent open defect for it is noise
        return {"status": "HISTORICAL", "dates": [], "quote": quote,
                "reason": "published more than %d days ago and not readable - recorded, not applied" % WINDOW_BACK_DAYS}
    if unreadable:
        return {"status": "PENDING", "dates": [], "quote": quote,
                "reason": "a date in the circular could not be read: " + unreadable[0].ambiguous}
    dates = sorted({d for s in closing for d in s.dates})
    if not dates:
        return {"status": "PENDING", "dates": [], "quote": quote,
                "reason": "no closing dates could be read (a lone date is a reopening or a working day, "
                          "not assumed to be a closure)"}
    lo, hi = today - datetime.timedelta(days=WINDOW_BACK_DAYS), today + datetime.timedelta(days=WINDOW_FORWARD_DAYS)
    if all(d < lo for d in dates):
        return {"status": "HISTORICAL", "dates": [], "quote": quote,
                "reason": f"every date is more than {WINDOW_BACK_DAYS} days old - recorded, not applied"}
    if any(d > hi or d < lo for d in dates):
        return {"status": "PENDING", "dates": [], "quote": quote, "reason": "a date is outside the window the "
                                                                          "system will act on without a human"}
    new = [d for d in dates if is_workday(d) and not is_closed(d)]
    if not new:
        return {"status": "APPLIED", "dates": [], "quote": quote,
                "reason": "every working day it names is already closed in the calendar"}
    # The market overrules a circular: if it traded on a date the circular closes, something is wrong.
    traded = [d for d in new if d < today and activity(d) > 0]
    if traded:
        return {"status": "REJECTED", "dates": [], "quote": quote,
                "reason": f"the circular closes {traded[0]} but the market traded that day"}
    # Corroboration for each NEW date, from something other than the circular.
    seg_of = {d: s for s in closing for d in s.dates}
    for d in new:
        if d < today:
            if last_observed is None or last_observed <= d:
                return {"status": "PENDING", "dates": [], "quote": quote,
                        "reason": f"{d} cannot be checked against the market yet (nothing observed after it)"}
            continue                                     # silent AND observed afterwards: corroborated
        extends_known = any(is_closed(x) for x in seg_of[d].dates)      # extends a run the gazette closes
        if not (general or extends_known):
            return {"status": "PENDING", "dates": [], "quote": quote,
                    "reason": f"{d} is still to come and is neither a declared general holiday nor part of a "
                              f"run the gazette already closes"}
    return {"status": "APPLIED", "dates": new, "quote": quote, "reason": "corroborated"}


# ── the world, read from the database ─────────────────────────────────────────

def _market_activity(session, d: datetime.date) -> int:
    from sqlalchemy import text
    n = 0
    for tbl, col, cond in (("call_money_rates", "trade_date", ""), ("ref_rates", "trade_date", ""),
                           ("omo_transactions", "transaction_date", " AND accepted_bdt_crore > 0")):
        n += session.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE {col} = :d{cond}"), {"d": d}).scalar() or 0
    return int(n)


def _last_observed(session) -> Optional[datetime.date]:
    from sqlalchemy import text
    best = None
    for tbl, col in (("call_money_rates", "trade_date"), ("ref_rates", "trade_date"),
                     ("omo_transactions", "transaction_date")):
        v = session.execute(text(f"SELECT MAX({col}) FROM {tbl}")).scalar()
        if isinstance(v, str):
            v = datetime.date.fromisoformat(v[:10])
        if v and (best is None or v > best):
            best = v
    return best


def apply_notice(session, notice, dates: List[datetime.date], quote: str, announced: datetime.date) -> List[str]:
    """The only writer of CIRCULAR_CLOSURE rows. Returns what it did, one line per date.

    A closure already on the list keeps its type (so the gazette and this never fight over a row);
    it only gains the circular as evidence. A DECLARED WORKING DAY is a conflict and is left alone."""
    import calendar_utils
    from config import fiscal_year
    from db import HolidayCalendar
    done = []
    for d in dates:
        cur = session.get(HolidayCalendar, d)
        if cur is None:
            session.add(HolidayCalendar(
                calendar_date=d, holiday_name=(notice.title or "Bank closure per BB circular")[:120],
                holiday_type=calendar_utils.CIRCULAR_CLOSURE_TYPE, fiscal_year=fiscal_year(d),
                source=notice.url[:200], evidence_url=notice.url, evidence_quote=quote[:400],
                announced_date=notice.published_date))
            done.append(f"+ {d} closed per {notice.url}")
        elif cur.holiday_type == calendar_utils.WORKING_DAY_TYPE:
            done.append(f"! {d} is a DECLARED WORKING DAY; the circular was not applied there")
    session.flush()
    return done


def _evidence_for_known_closures(session, notice, dates, quote):
    """Closures the gazette already holds gain the circular as their evidence (type untouched)."""
    from db import HolidayCalendar
    for d in dates:
        cur = session.get(HolidayCalendar, d)
        if cur is not None and not cur.evidence_url:
            cur.evidence_url, cur.evidence_quote, cur.announced_date = notice.url, quote[:400], notice.published_date


# ── human decisions ───────────────────────────────────────────────────────────

def load_decisions(path: str = DECISIONS_PATH):
    """(decisions, errors) from data/seeds/calendar_decisions.yaml.

    A decision settles a PENDING notice the code could not: `confirm` with the circular's url, the
    dates, and a quote from it. A decision missing any of these is an error and writes nothing."""
    if not os.path.exists(path):
        return [], []
    import yaml
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    good, errors = [], []
    for i, d in enumerate(doc.get("decisions") or []):
        miss = [k for k in ("url", "decision", "dates", "quote") if not d.get(k)]
        if miss or d["decision"] not in ("confirm", "reject"):
            errors.append(f"calendar_decisions.yaml entry {i + 1}: needs {', '.join(miss) or 'decision: confirm|reject'}")
            continue
        try:
            d["dates"] = [datetime.date.fromisoformat(str(x)) for x in d["dates"]]
        except ValueError:
            errors.append(f"calendar_decisions.yaml entry {i + 1}: dates must be YYYY-MM-DD")
            continue
        good.append(d)
    return good, errors


# ── orchestration ─────────────────────────────────────────────────────────────

def process(session, rows: List[Dict], read_pdf: Callable[[str], Optional[str]],
            today: datetime.date = None, now: datetime.datetime = None, decisions=None) -> Dict:
    """Run every listed circular through assess() and apply what is proven. Idempotent."""
    import calendar_utils
    from db import CalendarNotice
    today = today or datetime.date.today()
    now = now or datetime.datetime.utcnow()
    out = {"seen": len(rows), "applied": 0, "pending": 0, "rejected": 0, "historical": 0, "changed_calendar": False,
           "lines": [], "errors": []}
    by_decision = {d["url"]: d for d in (decisions or [])}
    first_run = session.query(CalendarNotice).count() == 0
    for r in rows:
        n = session.query(CalendarNotice).filter_by(url=r["url"]).one_or_none()
        if n is not None and n.status in ("APPLIED", "HISTORICAL", "REJECTED") and r["url"] not in by_decision:
            continue
        text_ = read_pdf(r["url"])
        if n is None:
            n = CalendarNotice(url=r["url"], published_date=r["published_date"], department=r["department"],
                               title=r["title"], kind="CLOSURE", first_seen_utc=now)
            session.add(n)
        dec = by_decision.get(r["url"])
        seg_dates: List[datetime.date] = []
        if dec:
            if dec["decision"] == "reject":
                verdict = {"status": "REJECTED", "dates": [], "quote": dec["quote"], "reason": "rejected by a human decision"}
            else:
                ds = sorted(dec["dates"])
                verdict = {"status": "APPLIED", "dates": [d for d in ds if calendar_utils.is_working_day(d)
                           and d not in calendar_utils.get_loaded_holidays()],
                           "quote": dec["quote"], "reason": "confirmed by a human decision"}
        elif text_ is None:
            out["errors"].append(f"calendar circular {r['url']}: PDF could not be read")
            n.status, n.reason = "PENDING", "the PDF could not be fetched or read"
            out["pending"] += 1
            continue
        else:
            segs = extract_segments(text_, today)
            seg_dates = [d for s in segs if s.kind in ("range", "list") for d in s.dates]
            verdict = assess(r["title"], segs, today,
                             is_workday=calendar_utils.is_working_day,
                             is_closed=lambda d: d in calendar_utils.get_loaded_holidays(),
                             activity=lambda d: _market_activity(session, d),
                             last_observed=_last_observed(session), published=r["published_date"])
        before = n.status
        n.status, n.reason, n.evidence_quote = verdict["status"], verdict["reason"], verdict["quote"]
        n.dates_json = json.dumps([str(d) for d in verdict["dates"]])
        n.decided_utc = now
        if verdict["status"] == "HISTORICAL" or (first_run and verdict["status"] != "PENDING"):
            n.notified_utc = now                # the backlog is recorded, not announced
        elif before != verdict["status"]:
            n.notified_utc = None               # a change of state is news again
        if verdict["status"] == "APPLIED":
            lines = apply_notice(session, n, verdict["dates"], verdict["quote"], n.published_date)
            _evidence_for_known_closures(session, n, seg_dates or verdict["dates"], verdict["quote"])
            out["lines"] += lines
            if any(l.startswith("+") for l in lines):
                out["changed_calendar"] = True
        out[verdict["status"].lower()] = out.get(verdict["status"].lower(), 0) + 1
    session.flush()
    return out


def run_circular_watch(today: datetime.date = None, fetch_listing=None, read_pdf=None) -> Dict:
    """Read BB's circulars, settle what is proven, and bring everything derived into line.

    A page that was not served changes nothing and is reported as an error (loud, not wrong)."""
    from db import get_session
    from fetchers import circulars as C
    fetch_listing = fetch_listing or C.fetch_listing
    read_pdf = read_pdf or C.read_pdf_text
    listing = fetch_listing()
    if listing.get("transport_error"):
        return {"rows": 0, "errors": [f"circulars: {listing['transport_error']}"], "applied": 0, "pending": 0}
    session = get_session()
    try:
        from engines.pipeline import _load_holidays, propagate_calendar_change
        _load_holidays(session)
        decisions, derrors = load_decisions()
        res = process(session, listing["rows"], read_pdf, today=today, decisions=decisions)
        session.commit()
        res["errors"] = res.get("errors", []) + derrors
        if res["changed_calendar"]:
            res["propagated"] = propagate_calendar_change(session)
        res["rows"] = len(listing["rows"])
        return res
    except Exception as exc:
        session.rollback()
        log.exception("circular watch failed")
        return {"rows": 0, "errors": [f"circulars: {exc}"], "applied": 0, "pending": 0}
    finally:
        session.close()


def main(argv=None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    from db import init_db
    init_db()                       # the calendar gained evidence columns; migrate before touching it
    res = run_circular_watch()
    for line in res.get("lines") or []:
        print(line)
    print({k: v for k, v in res.items() if k not in ("lines",)})
    return 1 if res.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
