"""Bangladesh Bank's published holiday list, read from BB's own page.

Nothing in this project has ever read it. `BB_HOLIDAY_URL` sat in config.py
unused, and `holiday_calendar` held 33 rows ending 26-Aug-2026 — the recent
ones marked INFERRED_CLOSURE, meaning "we noticed the whole money market went
silent and guessed a holiday". Guessing backwards works for the past and not at
all for the future, so on 15-Oct-2026 the system did not know that 20 and 21
October are Durga Puja: a Tuesday and a Wednesday, both bank holidays, five
days away. Every settlement date, maturity roll, auction date and working-day
age that crosses them was wrong.

BB states each holiday three times over, which is what makes this parseable
without guessing:

    Holiday                              Date                          Day                   No of Holidays
    Durga Puja (Nabami & Bijoya Dashami) 20 & 21 October 2026          Tuesday & Wednesday   2 Days
    *Eid-ul-Azha                         26, 27, 28, 29, 30 & 31 May.. Tuesday, Wednesday..  6 Days

The dates, the weekday names and the count must all agree. When they do, the
row is a fact; when they do not, the row is REFUSED and counted — never
half-parsed into a closure, because a wrong holiday is worse than a missing
one: it moves real settlement dates off real days.

Dates carry `holiday_type = 'GAZETTED'` to distinguish them from the inferred
closures already in the table and from declared working weekends
(`WORKING_DAY`), which BB announces separately and which this never touches.
"""
import datetime
import logging
import re
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("holidays")

BB_HOLIDAY_URL = "https://www.bb.org.bd/en/index.php/mediaroom/holiday"

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
_WEEKDAYS = {d.lower(): i for i, d in enumerate(
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}

# "20 & 21 October 2026" / "19, 20, 21, 22 & 23 March 2026" / "14 April, 2026"
_SEGMENT = re.compile(
    r"(?P<days>\d{1,2}(?:\s*(?:,|&|and)\s*\d{1,2})*)\s*"
    r"(?P<month>" + "|".join(_MONTHS) + r")\s*,?\s*(?P<year>\d{4})?",
    re.I)
_COUNT = re.compile(r"(\d+)\s*day", re.I)


def parse_date_cell(text: str) -> Tuple[List[datetime.date], Optional[str]]:
    """Expand BB's date string into real dates.

    Returns (dates, error). A string that does not parse cleanly returns no
    dates and says why — the one thing it must never do is return some of the
    dates, because the count check downstream would then pass on a half-read
    row.
    """
    raw = (text or "").strip()
    if not raw:
        return [], "empty date cell"
    out: List[datetime.date] = []
    year_seen: Optional[int] = None
    segments = list(_SEGMENT.finditer(raw))
    if not segments:
        return [], f"no 'day(s) month year' pattern in {raw!r}"
    # BB sometimes prints the year only once, at the end of the last segment
    # ("30 & 31 March, 1 April 2026"). Read it backwards so every segment has
    # one, rather than defaulting to the current year — which would silently
    # file a holiday twelve months out of place.
    for seg in reversed(segments):
        if seg.group("year"):
            year_seen = int(seg.group("year"))
            break
    if year_seen is None:
        return [], f"no year anywhere in {raw!r}"
    for seg in segments:
        year = int(seg.group("year")) if seg.group("year") else year_seen
        month = _MONTHS[seg.group("month").lower()]
        for d in re.findall(r"\d{1,2}", seg.group("days")):
            try:
                out.append(datetime.date(year, month, int(d)))
            except ValueError:
                return [], f"{d} {seg.group('month')} {year} is not a real date"
    uniq = sorted(set(out))
    if len(uniq) != len(out):
        return [], f"the same date appears twice in {raw!r}"
    return uniq, None


def parse_page(html: str) -> dict:
    """Parse BB's holiday table. Returns rows, refusals and a fingerprint."""
    from bs4 import BeautifulSoup
    from fetchers import source_watch as SW

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return {"rows": [], "refused": {}, "meta": SW.fingerprint(
            "holidays", BB_HOLIDAY_URL, error="the page carries no table")}
    table = max(tables, key=lambda t: len(t.find_all("tr")))

    trs = table.find_all("tr")
    header_rows, data_rows = [], []
    for tr in trs:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        looks_header = any(_MONTHS.get(w.lower()) for w in " ".join(texts).split()) is False \
            and not data_rows and not any(ch.isdigit() for ch in " ".join(texts))
        if looks_header:
            header_rows.append(tr)
        else:
            data_rows.append(texts)

    headers = SW.header_columns(header_rows)
    spec = (
        SW.Column("name", ["holiday"], required=True),
        SW.Column("date", ["date"], required=True),
        SW.Column("day", ["day"], none_of=["holidays"]),
        SW.Column("count", ["no of holidays"], fallbacks=[["holidays"]]),
    )
    idx, notes = SW.map_columns(headers, spec, "holidays")   # raises if BB moves

    rows: List[Dict] = []
    refused: Dict[str, int] = {}

    def refuse(why: str):
        refused[why] = refused.get(why, 0) + 1

    for cells in data_rows:
        def cell(f):
            i = idx.get(f)
            return cells[i] if (i is not None and i < len(cells)) else ""

        name = cell("name").strip()
        dates, err = parse_date_cell(cell("date"))
        if err:
            refuse(f"unreadable date ({err})")
            continue
        if not name:
            refuse("a date with no holiday name")
            continue

        # BB's own count, stated in words. Two statements of one fact.
        stated = _COUNT.search(cell("count") or "")
        if stated and int(stated.group(1)) != len(dates):
            refuse(f"{name}: read {len(dates)} date(s) but BB says "
                   f"{stated.group(1)} — refusing rather than guessing which")
            continue

        # BB's own weekday names, as a third statement. Only the first is
        # checked: BB truncates long lists, and a first-day match is enough to
        # catch a day/month swap, which is the error that matters.
        day_cell = (cell("day") or "").lower()
        first_named = next((_WEEKDAYS[w] for w in re.split(r"[^a-z]+", day_cell)
                            if w in _WEEKDAYS), None)
        if first_named is not None and dates and first_named != dates[0].weekday():
            refuse(f"{name}: {dates[0]} is a "
                   f"{dates[0].strftime('%A')} but BB prints {day_cell.split(',')[0]!r}")
            continue

        # BB marks a part-of-the-country holiday in the name itself, e.g.
        # "Chaitra Sankranti (only applicable for Rangamati, Khagrachari &
        # Bandarban Hill District)". Those are kept, typed apart, and are NOT
        # closures: the money market traded normally on 13-Apr-2026.
        regional = bool(re.search(r"only\s+(applicable|for)\b", name, re.I))
        for d in dates:
            rows.append({"calendar_date": d, "holiday_name": name,
                         "holiday_type": "REGIONAL_HOLIDAY" if regional else "GAZETTED",
                         "source": BB_HOLIDAY_URL})

    meta = SW.fingerprint("holidays", BB_HOLIDAY_URL, selector="largest table",
                          headers=headers, rows_seen=len(data_rows),
                          rows_parsed=len(rows), skips=refused,
                          unmapped=notes.get("unmapped"), mapped=idx)
    if refused:
        log.warning("holidays: refused %d row(s): %s", sum(refused.values()), refused)
    return {"rows": rows, "refused": refused, "meta": meta}


def fetch_holidays() -> dict:
    """Read BB's holiday page. No browser: the page is plain HTML."""
    from curl_cffi import requests as cr
    try:
        r = cr.get(BB_HOLIDAY_URL, impersonate="chrome", timeout=30)
        html = r.text or ""
    except Exception as exc:
        from fetchers import source_watch as SW
        return {"rows": [], "refused": {}, "reason": f"{type(exc).__name__} {exc}",
                "meta": SW.fingerprint("holidays", BB_HOLIDAY_URL,
                                       error=f"could not read the page: {exc}")}
    out = parse_page(html)
    if not out["rows"]:
        out["reason"] = (out["meta"].get("error")
                         or f"read {len(html):,} bytes and parsed no holiday rows")
    return out
