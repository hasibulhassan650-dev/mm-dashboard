"""The auction book: every treasury auction, with results where BB has published.

Auction information was spread across five pages -- cut-off yields on /yields,
monthly amounts on /schedule, daily outflow on /cashflows and /forecast, per-day
on /drilldown -- and no page listed the auctions themselves. The endpoint that
serves them existed and the frontend never called it.

TWO THINGS NAMED "OFFERED", AND THEY ARE NOT THE SAME NUMBER:

  auction_events.offered_amount_bdt_crore   BB's NOTIFIED target for the auction
  primary_yield_snapshots.offered_bdt_crore  the BIDS RECEIVED at that auction

They are returned here as `notified_crore` and `bids_crore` and never merged.
Collapsing them would silently turn a target into a cover ratio.

THE JOIN IS ON SETTLEMENT, NOT ON AUCTION DATE. auction_events keeps the
CALENDAR's auction date while primary_yield_snapshots derives its own from
what BB printed, and the two legitimately differ -- the 23-May-2026 pre-Eid
bills sit at auction_date 24-May in the calendar and 23-May in the results.
confirm_auctions_from_results sets settlement_date FROM the published issue
date, so (tenor, settlement_date) == (tenor, issue_date) is the dependable key.
Joining on auction_date would drop exactly the moved auctions that matter most.

Self-contained: the deployed API is rooted at api/ and cannot import anything
above it.
"""
import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

PRODUCTS = ("T_BOND", "T_BILL", "FRTB")
OTHER = "OTHER"
MAX_RANGE_DAYS = 8000          # the auction history runs to 2007


def _d(v):
    if v is None or hasattr(v, "year"):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


def _bucket(stype) -> str:
    return stype if stype in PRODUCTS else OTHER


def bid_to_cover(bids: Optional[float], accepted: Optional[float]) -> Optional[float]:
    """Bids received over amount accepted, or None when it cannot be known.

    Suppressed when bids EQUAL accepted. In the earlier era BB published a fixed
    weekly target in the same column rather than bids received -- 364D at 245 cr
    fully accepted, week after week, verified against BB's pages while fixing
    the duplicate-auction defect. A ratio of exactly 1.00 there is an artefact
    of that, not a fully-covered auction, and printing it would read as a
    genuine cover figure.
    """
    if bids is None or accepted is None or accepted <= 0:
        return None
    if abs(bids - accepted) < 1e-9:
        return None
    return round(bids / accepted, 3)


def resolve_window(session, months: Optional[int] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   today: datetime.date = None) -> Tuple[datetime.date, datetime.date]:
    """The window, defaulting FORWARD to the end of BB's published calendar.

    The old endpoint was `auction_date >= since` with no upper bound expressed
    and no forward reach, so PLANNED auctions -- the ones a desk actually needs
    to see -- never appeared. The default now runs from `months` back to
    whatever BB has published, so the next auctions are in the window without
    asking.
    """
    today = today or datetime.date.today()
    if date_from or date_to:
        f = _d(date_from) if date_from else today - datetime.timedelta(days=180)
        t = _d(date_to) if date_to else today + datetime.timedelta(days=180)
        if t < f:
            raise ValueError(f"date_to {t} is before date_from {f}")
        if (t - f).days > MAX_RANGE_DAYS:
            raise ValueError(f"range {f}..{t} exceeds {MAX_RANGE_DAYS} days")
        return f, t
    n = 6 if months is None else int(months)
    if not 1 <= n <= 240:
        raise ValueError(f"months must be 1..240, got {n}")
    horizon = session.execute(text("SELECT MAX(settlement_date) FROM auction_events")).scalar()
    end = _d(horizon) or today
    return today - datetime.timedelta(days=n * 30), max(end, today)


# primary_yield_snapshots is aggregated before the join so a duplicate result
# row can never multiply an auction into two. `results` counts what it found,
# so a duplicate shows up as a number rather than silently doubling a total.
_BOOK = """
    SELECT a.fiscal_year, a.auction_no, a.auction_date, a.settlement_date,
           a.security_type, a.tenor_label,
           a.offered_amount_bdt_crore  AS notified,
           a.accepted_amount_bdt_crore AS accepted,
           a.weighted_avg_yield_pct    AS war,
           a.outflow_status, a.roll_reason,
           r.cutoff, r.bids, r.res_accepted, r.results
    FROM auction_events a
    LEFT JOIN (
        SELECT tenor_label, issue_date,
               MAX(cutoff_yield_pct)  AS cutoff,
               MAX(offered_bdt_crore) AS bids,
               MAX(accepted_bdt_crore) AS res_accepted,
               COUNT(*) AS results
        FROM primary_yield_snapshots
        GROUP BY tenor_label, issue_date
    ) r ON r.tenor_label = a.tenor_label AND r.issue_date = a.settlement_date
    WHERE a.auction_date BETWEEN :f AND :t
    ORDER BY a.auction_date DESC, a.security_type, a.tenor_label"""


def auction_book(session, months: Optional[int] = None,
                 date_from: Optional[str] = None,
                 date_to: Optional[str] = None,
                 today: datetime.date = None) -> dict:
    """Every auction in the window, plus per-product and monthly roll-ups."""
    today = today or datetime.date.today()
    f, t = resolve_window(session, months, date_from, date_to, today)
    rows = session.execute(text(_BOOK), {"f": str(f), "t": str(t)}).fetchall()

    horizon = session.execute(text("SELECT MAX(settlement_date) FROM auction_events")).scalar()
    horizon = _d(horizon)

    auctions: List[dict] = []
    for r in rows:
        adate, settle = _d(r.auction_date), _d(r.settlement_date)
        notified = float(r.notified) if r.notified is not None else None
        accepted = (float(r.accepted) if r.accepted is not None
                    else float(r.res_accepted) if r.res_accepted is not None else None)
        bids = float(r.bids) if r.bids is not None else None
        # Prefer the cut-off BB printed; fall back to the weighted average the
        # calendar row carries, which confirm_auctions_from_results copied from
        # the same source.
        cutoff = (float(r.cutoff) if r.cutoff is not None
                  else float(r.war) if r.war is not None else None)
        auctions.append({
            "auction_date": str(adate) if adate else None,
            "settlement_date": str(settle) if settle else None,
            "month": settle.strftime("%Y-%m") if settle else None,
            "fiscal_year": r.fiscal_year,
            "auction_no": r.auction_no,
            "product": _bucket(r.security_type),
            "tenor_label": r.tenor_label,
            "notified_crore": notified,
            "bids_crore": bids,
            "accepted_crore": accepted,
            "cutoff_yield_pct": cutoff,
            "bid_to_cover": bid_to_cover(bids, accepted),
            # PLANNED is BB's calendar target; CONFIRMED is what BB accepted.
            # Carried so a plan is never read or summed as a settled amount.
            "status": r.outflow_status,
            "has_results": r.results is not None,
            "duplicate_results": int(r.results) > 1 if r.results is not None else False,
            "roll_reason": r.roll_reason,
            "is_past": bool(adate and adate < today),
        })

    def _roll(keyfn) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for a in auctions:
            k = keyfn(a)
            if k is None:
                continue
            s = out.setdefault(k, {"auctions": 0, "notified_crore": 0.0,
                                   "accepted_crore": 0.0, "planned": 0, "confirmed": 0,
                                   "_y": [], "_w": 0.0})
            s["auctions"] += 1
            s["notified_crore"] += a["notified_crore"] or 0.0
            s["accepted_crore"] += a["accepted_crore"] or 0.0
            if a["status"] == "CONFIRMED":
                s["confirmed"] += 1
            else:
                s["planned"] += 1
            if a["cutoff_yield_pct"] is not None and (a["accepted_crore"] or 0) > 0:
                s["_y"].append(a["cutoff_yield_pct"] * a["accepted_crore"])
                s["_w"] += a["accepted_crore"]
        for s in out.values():
            # Weighted by accepted amount: a 35,000 cr bill and a 500 cr FRTB
            # are not equal observations of "the average yield".
            s["avg_cutoff_pct"] = round(sum(s["_y"]) / s["_w"], 4) if s["_w"] else None
            s["notified_crore"] = round(s["notified_crore"], 2)
            s["accepted_crore"] = round(s["accepted_crore"], 2)
            s["accepted_share"] = (round(s["accepted_crore"] / s["notified_crore"], 3)
                                   if s["notified_crore"] else None)
            del s["_y"], s["_w"]
        return out

    by_product = _roll(lambda a: a["product"])
    by_month = _roll(lambda a: a["month"])

    return {
        "as_of": str(today), "from": str(f), "to": str(t), "unit": "BDT crore",
        "products": list(PRODUCTS) + [OTHER],
        "calendar_published_to": str(horizon) if horizon else None,
        "count": len(auctions),
        "auctions": auctions,
        "by_product": by_product,
        "months": [{"month": m, **by_month[m]} for m in sorted(by_month)],
        "totals": {
            "auctions": len(auctions),
            "notified_crore": round(sum(a["notified_crore"] or 0 for a in auctions), 2),
            "accepted_crore": round(sum(a["accepted_crore"] or 0 for a in auctions), 2),
            "planned": sum(1 for a in auctions if a["status"] != "CONFIRMED"),
            "confirmed": sum(1 for a in auctions if a["status"] == "CONFIRMED"),
        },
    }
