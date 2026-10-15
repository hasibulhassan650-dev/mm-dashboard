"""What Bangladesh Bank announced, and what it means for our data.

The listing is stored by the daily fetch (see fetchers/press.py). This serves
it back with a summary that is COMPUTED, never written: counts by category,
the latest release of each kind that matters to the desk, and — the part worth
having — which announcements our own data does not yet reflect.

No model is involved. The categories come from BB's own title text and the
"what this means" lines come from comparing the listing against the tables we
hold. A summary that paraphrased BB would be a new source of error sitting in
front of the real one.
"""
import datetime

from fastapi import APIRouter, Query
from sqlalchemy import text

from db import get_session

router = APIRouter()

# Categories the desk acts on, in the order it reads them.
_HEADLINE = ("BUYBACK", "AUCTION_NOTICE", "TBILL_RESULT", "TBOND_RESULT",
             "BGIIB", "OMO", "POLICY", "FX", "SUKUK", "OTHER")

_LABELS = {
    "BUYBACK":        "Bond buyback",
    "AUCTION_NOTICE": "Auction notice",
    "TBILL_RESULT":   "T-Bill auction result",
    "TBOND_RESULT":   "T-Bond auction result",
    "BGIIB":          "Islamic window (BGIIB)",
    "SUKUK":          "Sukuk",
    "OMO":            "Open market operations",
    "POLICY":         "Policy rate",
    "FX":             "FX / remittance",
    "OTHER":          "Other",
}


@router.get("")
def get_press(days: int = Query(60, ge=1, le=400), limit: int = Query(300, ge=1, le=1000)):
    """BB's press releases, newest first, with a computed summary."""
    session = get_session()
    try:
        today = datetime.date.today()
        since = today - datetime.timedelta(days=days)
        rows = session.execute(text("""
            SELECT serial, published_date, subject_date, title, url, category, isin
            FROM press_releases
            WHERE published_date >= :since
            ORDER BY published_date DESC, serial DESC
            LIMIT :limit
        """), {"since": str(since), "limit": limit}).fetchall()
        releases = [dict(r._mapping) for r in rows]

        week = today - datetime.timedelta(days=7)
        by_cat, latest = {}, {}
        for r in releases:
            c = r["category"] or "OTHER"
            pub = r["published_date"]
            if hasattr(pub, "isoformat"):
                pub_d = pub
            else:
                try:
                    pub_d = datetime.date.fromisoformat(str(pub)[:10])
                except ValueError:
                    pub_d = None
            b = by_cat.setdefault(c, {"category": c, "label": _LABELS.get(c, c),
                                      "total": 0, "last_7_days": 0})
            b["total"] += 1
            if pub_d and pub_d >= week:
                b["last_7_days"] += 1
            if c not in latest:
                latest[c] = r

        # What the listing says we should hold, against what we do hold. This
        # is the only part of a "news summary" worth automating: BB announced
        # its first bond buyback twice and nothing in this system reacted.
        follow_ups = []
        bb = session.execute(text(
            "SELECT COUNT(*) FROM press_releases WHERE category = 'BUYBACK' "
            "AND LOWER(title) NOT LIKE '%notice%'")).scalar() or 0
        bb_events = session.execute(text(
            "SELECT COUNT(*) FROM buyback_events")).scalar() or 0
        if bb > bb_events:
            follow_ups.append({
                "severity": "defect",
                "text": f"{bb} buyback result release(s) announced and {bb_events} buyback "
                        f"event(s) recorded — cash moved on a day the ladder does not know "
                        f"about."})
        bg = session.execute(text(
            "SELECT COUNT(*), MAX(published_date) FROM press_releases "
            "WHERE category IN ('BGIIB', 'SUKUK')")).fetchone()
        if bg and bg[0]:
            follow_ups.append({
                "severity": "notice",
                "text": f"{bg[0]} Islamic-window release(s) on record (latest {bg[1]}). The "
                        f"releases are captured; their auction figures are not stored "
                        f"anywhere yet, so BGIIB does not appear in any ladder or curve."})

        return {
            "as_of": str(today),
            "window_days": days,
            "count": len(releases),
            "by_category": [by_cat[c] for c in _HEADLINE if c in by_cat],
            "latest_by_category": {c: latest[c] for c in _HEADLINE if c in latest},
            "follow_ups": follow_ups,
            "releases": releases,
        }
    finally:
        session.close()
