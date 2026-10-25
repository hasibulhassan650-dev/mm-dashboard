"""One line of news per BB release, built from the tables we hold.

BB's press PDFs are not read: the buyback ones are Bengali in a legacy font and
a paraphrase would be a new source of error sitting in front of the real one.
Instead each release is joined to the figures our own parsers already took from
BB's structured pages, and the line says where it came from:

  table       figures we hold for exactly this release
  title       only what BB's own title states (auction notices)
  not_parsed  the release exists but we hold nothing for it - said, never guessed
  ambiguous   more than one stored event fits - none is picked

Computed on every request, never stored, so a revised release cannot leave a
stale line. A fixed number of queries whatever the release count.

api/ deploys rooted at api/ and cannot import db/engines/fetchers: stdlib and
sqlalchemy only; the session is a parameter.
"""
import datetime
import re
from typing import Dict, List, Optional

from sqlalchemy import bindparam, text

NOT_PARSED = "Not yet parsed - no stored figures for this release"


def _d(x) -> Optional[datetime.date]:
    """A date from a date OR an ISO string (SQLite returns strings); never dropped."""
    if x is None:
        return None
    if isinstance(x, datetime.datetime):
        return x.date()
    if isinstance(x, datetime.date):
        return x
    try:
        return datetime.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _cr(x) -> str:
    """Crore with grouping; a missing figure is a dash, never 0."""
    return "-" if x is None else f"{float(x):,.2f}"


def _out(headline, basis):
    return {"headline": headline, "headline_basis": basis}


def headline_bgiib(rows) -> Dict:
    if not rows:
        return _out(NOT_PARSED, "not_parsed")
    parts = []
    for r in sorted(rows, key=lambda r: r["tenor_months"] if r["tenor_months"] is not None else 0):
        epr = "" if r["war_epr_pct"] is None else f" at {float(r['war_epr_pct']):.2f}% EPR"
        parts.append(f"{r['tenor_label']} {_cr(r['accepted_crore'])} cr accepted{epr}")
    return _out(" · ".join(parts), "table")


def headline_buyback(events) -> Dict:
    if not events:
        return _out(NOT_PARSED, "not_parsed")
    if len(events) > 1:
        return _out(f"{len(events)} buyback events fit this release - not picking one",
                    "ambiguous")
    e = events[0]
    cash = ("cash paid not published" if e["cash_paid_crore"] is None
            else f"cash paid {_cr(e['cash_paid_crore'])} cr")
    bids = "" if e["bids_received_count"] is None else f", {e['bids_received_count']} bids"
    when = _d(e["settlement_date"])
    return _out(f"Accepted {_cr(e['accepted_face_crore'])} cr face of "
                f"{_cr(e['offered_face_crore'])} cr offered{bids}; settled "
                f"{when.strftime('%d-%b-%Y') if when else '-'}; {cash}", "table")


def headline_auction(rows) -> Dict:
    if not rows:
        return _out(NOT_PARSED, "not_parsed")
    parts = []
    for r in sorted(rows, key=lambda r: (r["original_tenor_years"] is None,
                                         r["original_tenor_years"] or 0)):
        tenor = r["original_tenor_label"] or r["tenor_label"]
        cut = "" if r["cutoff_yield_pct"] is None else f" cut-off {float(r['cutoff_yield_pct']):.4f}%"
        face = _cr(r["accepted_bdt_crore"])
        sale = ("sale value not published" if r["sale_value_bdt_crore"] is None
                else f"sale value {_cr(r['sale_value_bdt_crore'])} cr")
        parts.append(f"{tenor}{cut}, {face} cr accepted ({sale})")
    return _out(" · ".join(parts), "table")


def headline_omo(rows) -> Dict:
    if not rows:
        return _out(NOT_PARSED, "not_parsed")
    inj = sum(float(r["accepted_bdt_crore"]) for r in rows if r["direction"] == "INJECTION")
    ab = sum(float(r["accepted_bdt_crore"]) for r in rows if r["direction"] == "ABSORPTION")
    # Dealt amounts only. The net effect on liquidity also depends on tranches
    # maturing the same day (a tranche moves liquidity twice), which is the
    # ladder's job - restating it here is how a sign gets flipped.
    return _out(f"Injected {_cr(inj)} cr · absorbed {_cr(ab)} cr dealt "
                f"(net effect: see Cash Flows)", "table")


def headline_from_title(title: str, subject_date) -> Optional[Dict]:
    """Only what the title itself says. Nothing inferred."""
    t = re.sub(r"\s+", " ", title or "")
    bits = []
    m = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*year", t, re.I)
    if m:
        bits.append(f"{m.group(1)}-year")
    m = re.search(r"(\d+)\s*[- ]?\s*days?\b", t, re.I)
    if m:
        bits.append(f"{m.group(1)}-day")
    if re.search(r"re-?\s*issue", t, re.I):
        bits.append("re-issue")
    m = re.search(r"remaining\s+(?:maturity\s+)?(?:of\s+)?(\d+(?:\.\d+)?)\s*y", t, re.I)
    if m:
        bits.append(f"{m.group(1)}Y remaining")
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*coupon", t, re.I)
    if m:
        bits.append(f"{m.group(1)}% coupon")
    when = _d(subject_date)
    if not bits and not when:
        return None
    return _out(" · ".join(bits + ([f"auction {when.strftime('%d-%b-%Y')}"] if when else [])),
                "title")


def build_headlines(session, releases: List[Dict]) -> Dict[str, Dict]:
    """serial -> {headline, headline_basis}; absent when there is nothing honest to say."""
    if not releases:
        return {}
    pubs = [d for d in (_d(r["published_date"]) for r in releases) if d]
    subs = [d for d in (_d(r.get("subject_date")) for r in releases) if d]
    every = pubs + subs
    lo = str(min(every) - datetime.timedelta(days=21)) if every else None
    # a buyback settles up to 21 days AFTER its release, so the upper bound must reach there
    hi = str(max(every) + datetime.timedelta(days=22)) if every else None

    islamic = {r["serial"] for r in releases if r["category"] in ("BGIIB", "SUKUK")}
    bg: Dict[str, list] = {}
    if islamic:
        q = text("SELECT source_serial, tenor_label, tenor_months, accepted_crore, "
                 "war_epr_pct FROM bgiib_auctions WHERE source_serial IN :s"
                 ).bindparams(bindparam("s", expanding=True))
        for r in session.execute(q, {"s": sorted(islamic)}).fetchall():
            bg.setdefault(r[0], []).append(dict(r._mapping))

    events, auctions, omo = [], [], []
    if lo:
        events = [dict(r._mapping) for r in session.execute(text(
            "SELECT settlement_date, bids_received_count, offered_face_crore, "
            "accepted_face_crore, cash_paid_crore FROM buyback_events "
            "WHERE settlement_date BETWEEN :lo AND :hi"), {"lo": lo, "hi": hi})]
        auctions = [dict(r._mapping) for r in session.execute(text(
            "SELECT auction_date, tenor_label, original_tenor_label, original_tenor_years, "
            "cutoff_yield_pct, accepted_bdt_crore, sale_value_bdt_crore "
            "FROM primary_yield_snapshots WHERE auction_date BETWEEN :lo AND :hi"),
            {"lo": lo, "hi": hi})]
        omo = [dict(r._mapping) for r in session.execute(text(
            "SELECT transaction_date, direction, accepted_bdt_crore FROM omo_transactions "
            "WHERE transaction_date BETWEEN :lo AND :hi AND accepted_bdt_crore > 0"),
            {"lo": lo, "hi": hi})]

    out: Dict[str, Dict] = {}
    for rel in releases:
        cat, title = rel["category"], rel.get("title") or ""
        pub, sub = _d(rel["published_date"]), _d(rel.get("subject_date"))
        is_notice = "notice" in title.lower()
        h = None
        if cat in ("BGIIB", "SUKUK") and not is_notice:
            h = headline_bgiib(bg.get(rel["serial"], []))
        elif cat == "BUYBACK" and not is_notice:
            # same link rule validate.py uses: settles within [pub-3d, pub+21d]
            h = headline_buyback([e for e in events if pub and _d(e["settlement_date"])
                                  and pub - datetime.timedelta(days=3) <= _d(e["settlement_date"])
                                  <= pub + datetime.timedelta(days=21)])
        elif cat in ("TBILL_RESULT", "TBOND_RESULT") and not is_notice:
            # "held on" is the day bids were taken = auction_date (BB prints the
            # next working day as the issue date, which is NOT this).
            if sub:
                day = [a for a in auctions if _d(a["auction_date"]) == sub]
                # BB posts a separate release per tenor on the same day; when the
                # title names one ("10-Year ..."), show only that tenor's line.
                m = re.match(r"\s*(\d+)\s*-?\s*year", title, re.I)
                if m:
                    mine = [a for a in day if a["original_tenor_years"] is not None
                            and round(float(a["original_tenor_years"])) == int(m.group(1))]
                    day = mine or day
                h = headline_auction(day)
        elif cat == "OMO" and sub:
            h = headline_omo([o for o in omo if _d(o["transaction_date"]) == sub])
        elif cat in ("AUCTION_NOTICE", "BUYBACK", "BGIIB", "SUKUK"):
            h = headline_from_title(title, sub)
        if h:
            out[rel["serial"]] = h
    return out
