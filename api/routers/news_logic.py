"""Liquidity Watch for the desk: events in the news that BB's own data does not hold.

Everything here is READ-ONLY and none of it is added into any ladder or total: these
are events a language model read in the press, with the quote and the links beside
them. The desk sees them next to - never inside - the figures BB publishes.

api/ deploys rooted at api/: stdlib and sqlalchemy only.
"""
import datetime
import json
from typing import Dict, List, Optional

from sqlalchemy import text

_COLS = ("id, kind, direction, amount_crore, amount_unverified, is_cumulative, event_date, announced_date, status, "
         "channel, counterparty, summary, evidence_quote, sources_json, confidence, matters, "
         "redundant_with, first_seen_utc, verified_by")


def _d(v) -> Optional[datetime.date]:
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def fiscal_year_start(today: datetime.date) -> datetime.date:
    """Bangladesh's fiscal year runs 1 July - 30 June."""
    return datetime.date(today.year if today.month >= 7 else today.year - 1, 7, 1)


def _row(r) -> Dict:
    m = dict(r._mapping)
    try:
        m["sources"] = json.loads(m.pop("sources_json") or "[]")
    except ValueError:
        m["sources"] = []
    for k in ("event_date", "announced_date"):
        m[k] = str(_d(m[k])) if _d(m[k]) else None
    m["first_seen_utc"] = str(m["first_seen_utc"]) if m.get("first_seen_utc") else None
    m["matters"] = bool(m["matters"])
    m["is_cumulative"] = bool(m.get("is_cumulative"))
    m["when"] = m["event_date"] or m["announced_date"] or (m["first_seen_utc"] or "")[:10] or None
    return m


def news_block(session, today: datetime.date = None, days: int = 120,
               show_redundant: bool = False, show_minor: bool = False, limit: int = 300) -> Dict:
    today = today or datetime.date.today()
    since = today - datetime.timedelta(days=days)
    rows = [_row(r) for r in session.execute(text(
        f"SELECT {_COLS} FROM liquidity_events ORDER BY COALESCE(event_date, announced_date) DESC, id DESC"
    )).fetchall()]
    rows = [r for r in rows if r["when"] and r["when"] >= str(since)]

    counted = [r for r in rows if r["matters"] and not r["redundant_with"]
               and r["direction"] in ("INJECT", "ABSORB")]
    # A running total ("Tk 47bn until mid-August") is listed but never added: the next
    # article's "Tk 70bn until September" would count the first 47bn twice.
    known = [r for r in counted if r["amount_crore"] is not None and not r["is_cumulative"]]

    monthly: Dict[str, Dict] = {}
    for r in known:
        b = monthly.setdefault(r["when"][:7], {"month": r["when"][:7], "inject_crore": 0.0,
                                               "absorb_crore": 0.0, "events": 0})
        b["inject_crore" if r["direction"] == "INJECT" else "absorb_crore"] += float(r["amount_crore"])
        b["events"] += 1
    for b in monthly.values():
        b["inject_crore"], b["absorb_crore"] = round(b["inject_crore"], 2), round(b["absorb_crore"], 2)

    fy = str(fiscal_year_start(today))
    this_month = today.strftime("%Y-%m")

    def tot(sel, d):
        return round(sum(float(r["amount_crore"]) for r in known
                         if r["direction"] == d and sel(r["when"])), 2)

    shown = [r for r in rows if (show_minor or r["matters"]) and (show_redundant or not r["redundant_with"])]

    last = session.execute(text("SELECT MAX(fetched_utc) FROM news_articles")).scalar()
    waiting = session.execute(text(
        "SELECT COUNT(*) FROM news_articles WHERE triage_status IN ('untriaged', 'error')")).scalar() or 0
    counts = {k: n for k, n in session.execute(text(
        "SELECT triage_status, COUNT(*) FROM news_articles GROUP BY triage_status")).fetchall()}
    reader_row = session.execute(text(
        "SELECT triage_model FROM news_articles WHERE triage_status = 'extracted' "
        "AND triage_model IS NOT NULL ORDER BY triage_utc DESC LIMIT 1")).fetchone()
    reader = ("none yet" if not reader_row else
              "rules" if str(reader_row[0]).startswith("rules") else "claude")
    return {
        "as_of": str(today), "window_days": days,
        "fiscal_year_start": fy,
        "totals": {
            "month_inject_crore": tot(lambda w: w[:7] == this_month, "INJECT"),
            "month_absorb_crore": tot(lambda w: w[:7] == this_month, "ABSORB"),
            "fy_inject_crore": tot(lambda w: w >= fy, "INJECT"),
            "fy_absorb_crore": tot(lambda w: w >= fy, "ABSORB"),
            "events_counted": len(counted),
            # events that matter but state no taka amount: counted as events, never as zero
            "events_without_amount": sum(1 for r in counted if r["amount_crore"] is None),
            "events_cumulative": sum(1 for r in counted if r["is_cumulative"]),
        },
        "monthly": [monthly[k] for k in sorted(monthly)],
        "hidden": {"redundant": sum(1 for r in rows if r["redundant_with"]),
                   "minor": sum(1 for r in rows if not r["matters"] and not r["redundant_with"])},
        "health": {
            "last_fetch_utc": str(last) if last else None,
            "reader": reader,
            "articles_waiting_for_claude": int(waiting),
            "articles_by_status": counts,
        },
        "count": len(shown),
        "events": shown[:limit],
        "note": ("Read from the press by a language model, then checked by code: the quote must be in "
                 "the article and the amount is re-read from it. NOT Bangladesh Bank data and NOT "
                 "included in any ladder, cumulative or model total."),
    }


def events_on(session, day) -> List[Dict]:
    """Matters-and-not-redundant events dated `day`, for the day page. Listed, never summed."""
    d = str(_d(day))
    return [_row(r) for r in session.execute(text(
        f"SELECT {_COLS} FROM liquidity_events WHERE matters IS TRUE AND redundant_with IS NULL "
        "AND COALESCE(event_date, announced_date) = :d ORDER BY id"), {"d": d}).fetchall()]
