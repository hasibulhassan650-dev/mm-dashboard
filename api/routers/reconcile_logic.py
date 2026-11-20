"""News vs Bangladesh Bank's FX-auction table: is a dollar sale the press reports on BB's page?

Why: BB posts its FX auction results late and in batches, so a sale reported on the day (16-Nov $100m, 18-Nov $220m) was
absent from our table for days while nothing said so; and the news-event 'already held' test only looked for ANY sale
within +-3 days, so one old row hid a missing new one. News figures are NEVER written into a BB table: this module only
READS both sides and says which one is behind.

Verdicts (per distinct news claim):
  MATCH        BB's row agrees on the amount (within max(1, 1%) USD mn) - nothing to say
  WAITING      no BB row yet, BB's page has nothing newer than the claim, and our fetch of it succeeded since -> BB is late
  UNCONFIRMED  still no BB row after BATCH_DAYS, or BB listed LATER auctions but not this one (BB also sells outside
               auctions, so this is a notice to look at, never a defect)
  CONFLICT     a BB row exists for that day but no outlet's figure agrees with it (BB's row stands; both are shown)
A claim is dropped silently when our own fetch has not succeeded since (our outage is not BB lagging: the freshness rules
own that) or when the title carries no parseable dollar amount.

Standalone on purpose: api/ cannot import root modules, and validate.py loads this file by path (no package imports).
"""
import datetime
import re

from sqlalchemy import text

WINDOW_DAYS = 3          # a story is published on the auction day or up to this many days after it
BATCH_DAYS = 10          # BB has been seen to post FX auction results a week or more late
ABS_TOL_MN = 1.0         # news rounds to whole millions
REL_TOL = 0.01
DHAKA = datetime.timedelta(hours=6)

_BB = re.compile(r"\b(?:bb|bangladesh bank|cenbank|central bank)\b", re.I)
_SELL = re.compile(r"\b(?:sells?|sold)\b", re.I)
_BUY = re.compile(r"\b(?:buys?|bought|purchases?)\b", re.I)
_USD1 = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?(?:m\b|mn\b|million\b)", re.I)
_USD2 = re.compile(r"\b(\d+(?:\.\d+)?)\s?(?:m|mn|million)\s+dollars?\b", re.I)


def claim_from_title(title: str):
    """('sell'|'buy', USD mn) when a headline says BB sold/bought a stated number of dollars, else None. Code, not a model:
    one unambiguous amount or nothing (two different amounts in a headline is not a claim we will pick between)."""
    t = title or ""
    if not _BB.search(t):
        return None
    side = "sell" if _SELL.search(t) else "buy" if _BUY.search(t) else None
    if side is None:
        return None
    amounts = {float(m) for m in _USD1.findall(t)} | {float(m) for m in _USD2.findall(t)}
    if len(amounts) != 1:
        return None
    return side, amounts.pop()


def _d(v):
    return v if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime) else datetime.date.fromisoformat(str(v)[:10])


def _dt(v):
    return v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v)[:19])


def _agrees(news_mn: float, bb_mn) -> bool:
    return bb_mn is not None and abs(news_mn - float(bb_mn)) <= max(ABS_TOL_MN, REL_TOL * float(bb_mn))


def reconcile_fx_news(session, today: datetime.date, lookback_days: int = 45) -> list:
    """List of dicts {verdict, side, date, amount_usd_mn, bb_amount_usd_mn, bb_date, urls, titles, note}, newest first.
    MATCH rows are included so a page can count them; callers decide what to show."""
    since = today - datetime.timedelta(days=lookback_days)
    bb = [(_d(r[0]), str(r[1] or "").lower(), r[2]) for r in session.execute(text(
        "SELECT auction_date, auction_type, accepted_amount_usd_mill FROM fx_auction_results")).fetchall()]
    bb_last = max((b[0] for b in bb), default=None)
    arts = session.execute(text(
        "SELECT published_utc, title, url FROM news_articles WHERE published_utc >= :s AND "
        "(LOWER(title) LIKE '%sell%' OR LOWER(title) LIKE '%sold%' OR LOWER(title) LIKE '%buy%' OR LOWER(title) LIKE '%bought%' "
        "OR LOWER(title) LIKE '%purchase%') ORDER BY published_utc"), {"s": str(since)}).fetchall()
    try:
        ok_runs = [_dt(r[0]) for r in session.execute(text(
            "SELECT run_utc FROM step_runs WHERE step = 'fx' AND status = 'ok'")).fetchall()]
    except Exception:
        session.rollback()
        ok_runs = []
    last_ok = max(ok_runs, default=None)

    claims = []
    for pub, title, url in arts:
        c = claim_from_title(title)
        if c is None:
            continue
        claims.append({"side": c[0], "amt": c[1], "date": (_dt(pub) + DHAKA).date(), "url": url, "title": title})

    # which BB rows does some outlet's figure agree with? A lone differing outlet beside an agreeing one is noise.
    agreed_rows = set()
    for c in claims:
        for b in bb:
            if b[1] == c["side"] and c["date"] - datetime.timedelta(days=WINDOW_DAYS) <= b[0] <= c["date"] and _agrees(c["amt"], b[2]):
                agreed_rows.add((b[0], b[1]))

    # one entry per distinct claim (same side, amount, within a day): the same sale reported by five outlets
    groups: list = []
    for c in claims:
        for g in groups:
            if g["side"] == c["side"] and g["amt"] == c["amt"] and abs((g["date"] - c["date"]).days) <= 1:
                g["urls"].append(c["url"]); g["titles"].append(c["title"]); break
        else:
            groups.append({**c, "urls": [c["url"]], "titles": [c["title"]]})

    out = []
    for g in groups:
        lo = g["date"] - datetime.timedelta(days=WINDOW_DAYS)
        rows = [b for b in bb if b[1] == g["side"] and lo <= b[0] <= g["date"]]
        base = {"side": g["side"], "date": str(g["date"]), "amount_usd_mn": g["amt"], "urls": g["urls"][:5],
                "titles": g["titles"][:3], "bb_amount_usd_mn": None, "bb_date": None, "note": ""}
        hit = next((b for b in rows if _agrees(g["amt"], b[2])), None)
        if hit:
            out.append({**base, "verdict": "MATCH", "bb_amount_usd_mn": float(hit[2]), "bb_date": str(hit[0])})
        elif rows:
            if any((b[0], b[1]) in agreed_rows for b in rows):
                continue                                   # another outlet's figure agrees with BB's row for that day
            b = rows[-1]
            out.append({**base, "verdict": "CONFLICT", "bb_amount_usd_mn": None if b[2] is None else float(b[2]),
                        "bb_date": str(b[0]),
                        "note": "BB's row stands; no outlet's figure agrees with it"})
        else:
            if last_ok is None or (last_ok + DHAKA).date() < g["date"]:
                continue                                   # our fetch has not succeeded since: not BB's lag to report
            age = (today - g["date"]).days
            later = bb_last is not None and bb_last > g["date"]
            if later or age > BATCH_DAYS:
                out.append({**base, "verdict": "UNCONFIRMED", "bb_date": None if bb_last is None else str(bb_last),
                            "note": "BB's FX auction page still lacks it" + (" although it lists later auctions" if later else "")
                                    + " (BB also sells dollars outside auctions)"})
            else:
                out.append({**base, "verdict": "WAITING", "bb_date": None if bb_last is None else str(bb_last),
                            "note": f"BB's FX auction page last lists {bb_last}"})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out
