"""The Islamic window: Bangladesh Government Islami Investment Bond (BGIIB).

BB auctions BGIIB weekly and publishes the result as a press release. Thirteen
of them were sitting in `press_releases` with their figures stored nowhere, so
the Islamic window did not appear in any ladder, any curve or any export — a
whole instrument absent from a dashboard whose first rule is to never miss a
product.

The releases are in English and properly tabular, which is a mercy after the
Bengali buyback notices:

    PARTICULARS      BIDS OFFERED        BIDS ACCEPTED
                     Total  Cumulative   Total  Cumulative  Accepted  Weighted  Range of   Range of
                     Bids   Offered      Bids   Accepted    PSR       Avg EPR   Base Rate  EPR
    3-Month BGIIB    5      670.00       5      670.00      90:10     7.40      7.00-9.50  6.30-8.55
    6-Month BGIIB    5      313.04       5      313.04      90:10     6.90      5.25-12.00 4.73-10.80
    Total            10     983.04       10     983.04

Two things to know about the numbers. **EPR** is the Expected Profit Rate —
the Islamic-finance counterpart of a yield, and what the desk compares against
a T-Bill cut-off. **PSR** is the profit-sharing ratio between the investor and
the government (90:10 above), which is not a rate at all and must never be
read as one.

BB prints a Total line, so every parse is checked against BB's own arithmetic
before it is believed. A release whose rows do not add up to its stated total
is REFUSED whole and counted: a misparse of one line would misstate the size
of the entire Islamic window, and showing nothing is better than showing that.
"""
import datetime
import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger("islamic")

# "3-Month BGIIB 5 670.00 5 670.00 90:10 7.40 7.00-9.50 6.30-8.55"
_ROW = re.compile(
    r"^(?P<months>\d{1,2})\s*-\s*Month\s+BGIIB\s+"
    r"(?P<nbid_off>\d+)\s+(?P<off>[\d,]+\.\d+|[\d,]+)\s+"
    r"(?P<nbid_acc>\d+)\s+(?P<acc>[\d,]+\.\d+|[\d,]+)\s+"
    r"(?P<psr>\d+\s*:\s*\d+)\s+"
    r"(?P<epr>\d+\.\d+)\s+"
    # A range OR a single value: with one bid there is no range, and BB prints
    # the one rate ("1 100.00 1 100.00 90:10 8.80 9.80 8.82"). Requiring "a-b"
    # dropped those lines, and because BB states a Total the whole release was
    # then refused — the guard doing its job on an incomplete parser of mine.
    r"(?P<base_range>[\d.]+(?:\s*-\s*[\d.]+)?)\s+"
    r"(?P<epr_range>[\d.]+(?:\s*-\s*[\d.]+)?)\s*$", re.I)

# "Total 10 983.04 10 983.04"
_TOTAL = re.compile(
    r"^Total\s+(?P<nbid_off>\d+)\s+(?P<off>[\d,]+\.\d+|[\d,]+)\s+"
    r"(?P<nbid_acc>\d+)\s+(?P<acc>[\d,]+\.\d+|[\d,]+)", re.I)

# "...Auction Result held on 13 October 2026"
_HELD = re.compile(r"held\s+on\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", re.I)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _num(s: str) -> float:
    return float(str(s).replace(",", "").strip())


def auction_date_from_text(text: str) -> Optional[datetime.date]:
    """The date the auction was HELD, from BB's own heading.

    Not the publication date: the 13-Oct auction was published on the 14th,
    and filing it under the 14th would put the Islamic window a day away from
    every other series it is compared against.
    """
    m = _HELD.search(text or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    try:
        return datetime.date(int(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None


def parse_bgiib_pdf(data: bytes, url: str = "",
                    fallback_date: datetime.date = None) -> dict:
    """Parse one BGIIB result PDF. Returns {"rows", "refused", "auction_date"}."""
    import io as _io

    import pdfplumber

    refused: Dict[str, int] = {}

    def refuse(why: str):
        refused[why] = refused.get(why, 0) + 1

    try:
        with pdfplumber.open(_io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as exc:
        return {"rows": [], "refused": {f"the PDF would not open ({exc})": 1},
                "auction_date": None}

    held = auction_date_from_text(text) or fallback_date
    if held is None:
        return {"rows": [], "auction_date": None,
                "refused": {"no 'held on <date>' in the release, and no date to fall "
                            "back on": 1}}

    rows: List[Dict] = []
    total = None
    for line in (re.sub(r"\s+", " ", l).strip() for l in text.split("\n")):
        m = _ROW.match(line)
        if m:
            months = int(m.group("months"))
            rows.append({
                "auction_date": held,
                "tenor_label": f"{months}M",
                "tenor_months": months,
                "bids_offered": int(m.group("nbid_off")),
                "offered_crore": _num(m.group("off")),
                "bids_accepted": int(m.group("nbid_acc")),
                "accepted_crore": _num(m.group("acc")),
                # Not a rate. 90:10 is how the profit is split with the
                # government, and reading it as a percentage would be nonsense.
                "psr": re.sub(r"\s+", "", m.group("psr")),
                "war_epr_pct": _num(m.group("epr")),
                "base_rate_range": re.sub(r"\s+", "", m.group("base_range")),
                "epr_range": re.sub(r"\s+", "", m.group("epr_range")),
                "source": url,
            })
            continue
        t = _TOTAL.match(line)
        if t:
            total = {"bids_offered": int(t.group("nbid_off")), "offered": _num(t.group("off")),
                     "bids_accepted": int(t.group("nbid_acc")), "accepted": _num(t.group("acc"))}

    if not rows:
        refuse("no '<n>-Month BGIIB' line matched — the layout has changed")
        return {"rows": [], "refused": refused, "auction_date": held}

    # BB states the total. Two statements of one fact, so check them against
    # each other before believing either.
    if total is not None:
        sums = {"bids_offered": sum(r["bids_offered"] for r in rows),
                "offered": round(sum(r["offered_crore"] for r in rows), 2),
                "bids_accepted": sum(r["bids_accepted"] for r in rows),
                "accepted": round(sum(r["accepted_crore"] for r in rows), 2)}
        bad = {k: (sums[k], total[k]) for k in sums
               if abs(sums[k] - total[k]) > (0.02 if isinstance(total[k], float) else 0)}
        if bad:
            refuse(f"the rows do not add up to BB's own Total line ({bad}) — refusing "
                   f"the whole release rather than misstating the window")
            return {"rows": [], "refused": refused, "auction_date": held}
    else:
        refuse("no Total line to check the rows against")

    return {"rows": rows, "refused": refused, "auction_date": held}


def fetch_bgiib(session, limit: int = 12) -> dict:
    """Parse the BGIIB releases already captured in `press_releases`.

    No new listing fetch: the press step records every release, so the work
    here is reading the PDFs we already know about — newest first, and only
    those with no stored auction yet.
    """
    from sqlalchemy import text as _t

    from config import BB_PRESS_URL
    from fetchers.bb_session import bb_get_bytes, get_f5_cookies

    pending = session.execute(_t("""
        SELECT p.serial, p.published_date, p.subject_date, p.title, p.url
        FROM press_releases p
        WHERE p.category IN ('BGIIB', 'SUKUK')
          AND NOT EXISTS (SELECT 1 FROM bgiib_auctions b
                          WHERE b.source_serial = p.serial)
        ORDER BY p.published_date DESC
        LIMIT :lim
    """), {"lim": limit}).fetchall()

    if not pending:
        return {"rows": [], "releases": 0, "refused": {}, "reason": None}

    try:
        ck, ua = get_f5_cookies(BB_PRESS_URL)
    except Exception as exc:
        return {"rows": [], "releases": 0, "refused": {},
                "reason": f"could not pass the F5 challenge: {type(exc).__name__} {exc}"}

    out: List[Dict] = []
    refused: Dict[str, int] = {}
    done = 0
    for serial, pub, subj, title, url in pending:
        try:
            data = bb_get_bytes(url, ck, ua)
        except Exception as exc:
            refused[f"{serial}: download failed ({type(exc).__name__})"] = 1
            continue
        if not data[:5].startswith(b"%PDF"):
            refused[f"{serial}: not a PDF"] = 1
            continue
        fb = subj if hasattr(subj, "year") else None
        rep = parse_bgiib_pdf(data, url, fallback_date=fb)
        for k, v in rep["refused"].items():
            refused[f"{serial}: {k}"] = v
        for r in rep["rows"]:
            r["source_serial"] = serial
            out.append(r)
        if rep["rows"]:
            done += 1
            log.info("  %s: %d BGIIB tenor(s) for %s", serial, len(rep["rows"]),
                     rep["auction_date"])
    return {"rows": out, "releases": done, "refused": refused,
            "reason": None if out else "no BGIIB figures parsed from any release"}
