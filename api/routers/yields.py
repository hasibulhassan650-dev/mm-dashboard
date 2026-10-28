from fastapi import APIRouter, Query
from sqlalchemy import text
from typing import Optional
from db import get_session

router = APIRouter()


@router.get("")
def get_yields(
    security_type: Optional[str] = None,
    tenor: Optional[str] = None,
    months: int = Query(12, ge=1, le=600),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """
    Primary market yield history.

    By default returns the last `months` months. Pass `date_from` / `date_to`
    (YYYY-MM-DD) for an explicit window — supports the full 2007→present range.
    """
    import datetime
    session = get_session()
    try:
        params: dict = {}
        clauses = []
        if date_from or date_to:
            if date_from:
                clauses.append("auction_date >= :dfrom"); params["dfrom"] = date_from
            if date_to:
                clauses.append("auction_date <= :dto"); params["dto"] = date_to
        else:
            since = datetime.date.today().replace(day=1)
            for _ in range(months - 1):
                since = (since - datetime.timedelta(days=1)).replace(day=1)
            clauses.append("auction_date >= :since"); params["since"] = str(since)

        if security_type:
            clauses.append("security_type = :stype"); params["stype"] = security_type.upper()
        if tenor:
            clauses.append("tenor_label = :tenor"); params["tenor"] = tenor.upper()

        q = """
            SELECT snapshot_date, auction_date, issue_date, security_type, tenor_label,
                   tenor_years, cutoff_yield_pct, offered_bdt_crore, accepted_bdt_crore
            FROM primary_yield_snapshots
        """
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY auction_date DESC, tenor_years"
        rows = session.execute(text(q), params).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()


@router.get("/range")
def get_yield_range():
    """Available auction-date span + row count (for the date-range picker bounds)."""
    session = get_session()
    try:
        row = session.execute(text("""
            SELECT MIN(auction_date) AS min_date, MAX(auction_date) AS max_date, COUNT(*) AS n
            FROM primary_yield_snapshots
        """)).fetchone()
        m = row._mapping
        return {"min": str(m["min_date"]) if m["min_date"] else None,
                "max": str(m["max_date"]) if m["max_date"] else None,
                "count": m["n"]}
    finally:
        session.close()


# A tenor is on today's curve only if it has actually auctioned recently. Without this the "latest row
# per tenor" kept a 14-day bill last auctioned in Sep-2025 (10.05%) and a 28-day bill from 2008 (7.5%)
# on the curve, and the long-end KPI read a year-old number. Bonds can go two months between
# auctions, so the limit is generous; a tenor BB starts auctioning again re-enters by itself.
LIVE_TENOR_DAYS = 120


def _latest_per_tenor(session):
    """(live, dormant): each tenor's most recent cut-off, split by how long ago it last auctioned."""
    rows = session.execute(text("""
        SELECT p.tenor_label, p.tenor_years, p.security_type, p.cutoff_yield_pct, p.auction_date
        FROM primary_yield_snapshots p
        WHERE p.cutoff_yield_pct IS NOT NULL
          AND p.auction_date = (SELECT MAX(q.auction_date) FROM primary_yield_snapshots q
                                WHERE q.tenor_label = p.tenor_label AND q.cutoff_yield_pct IS NOT NULL)
    """)).fetchall()
    seen, latest = set(), []
    for r in rows:                       # two same-day BB auctions of one tenor share a label: keep one
        m = dict(r._mapping)
        if m["tenor_label"] not in seen:
            seen.add(m["tenor_label"])
            latest.append(m)
    if not latest:
        return [], []
    newest = max(_as_date(m["auction_date"]) for m in latest)
    live, dormant = [], []
    for m in latest:
        m["age_days"] = (newest - _as_date(m["auction_date"])).days
        (live if m["age_days"] <= LIVE_TENOR_DAYS else dormant).append(m)
    key = lambda x: x["tenor_years"] or 0
    return sorted(live, key=key), sorted(dormant, key=key)


def _as_date(v):
    import datetime
    return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])


@router.get("/curve")
def get_yield_curve():
    """Latest yield curve - the most recent auction per tenor, for tenors that auctioned recently."""
    session = get_session()
    try:
        return _latest_per_tenor(session)[0]
    finally:
        session.close()


@router.get("/curve/dormant")
def get_dormant_tenors():
    """Tenors in the history that have not auctioned for LIVE_TENOR_DAYS: kept out of the curve and the
    KPIs (their last yield is history, not today's market) but listed, so nothing is dropped silently."""
    session = get_session()
    try:
        return _latest_per_tenor(session)[1]
    finally:
        session.close()


@router.get("/curve/provisional")
def get_provisional_curve():
    """Desk cut-off marks for auctions BB has not published yet, one per tenor.

    The desk reads the cut-off when the auction closes; BB prints it the next working day or
    later. Until then the curve above is a day or more behind what the desk knows, so the pages
    draw these as a clearly separate, provisional layer on top of it. A mark appears only while it
    is NEWER than BB's latest published auction for that tenor: the moment BB publishes (or a later
    auction does) it drops out and BB's figure stands alone. Nothing here is ever BB data, and
    nothing here feeds a spread, slope or history."""
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT d.tenor_label, d.security_type, d.cutoff_yield_pct, d.auction_date,
                   d.entered_by, d.updated_utc,
                   y.tenor_years, y.cutoff_yield_pct AS bb_yield_pct, y.auction_date AS bb_auction_date
            FROM desk_marks d
            JOIN primary_yield_snapshots y
              ON y.tenor_label = d.tenor_label
             AND y.cutoff_yield_pct IS NOT NULL
             AND y.auction_date = (SELECT MAX(p.auction_date) FROM primary_yield_snapshots p
                                   WHERE p.tenor_label = d.tenor_label AND p.cutoff_yield_pct IS NOT NULL)
            WHERE d.auction_date > y.auction_date
              AND d.auction_date = (SELECT MAX(m.auction_date) FROM desk_marks m
                                    WHERE m.tenor_label = d.tenor_label)
        """)).fetchall()
        out, seen = [], set()
        for r in rows:                     # two same-day BB auctions of one tenor share a label
            m = dict(r._mapping)
            if m["tenor_label"] in seen:
                continue
            seen.add(m["tenor_label"])
            m["diff_bps_vs_bb_latest"] = round((float(m["cutoff_yield_pct"]) - float(m["bb_yield_pct"])) * 100, 1)
            m["source"] = "DESK (provisional)"
            out.append(m)
        return sorted(out, key=lambda x: x["tenor_years"] or 0)
    finally:
        session.close()


@router.get("/slope")
def get_curve_slope(months: int = Query(24, ge=3, le=60)):
    """
    Yield-curve slope over time, built from primary auction yields.
    Tenors auction on different dates, so for each date with a new print we
    forward-fill the latest available yield per key tenor, then compute spreads.
    Returns: [{date, y_91d, y_2y, y_10y, two_ten, short_ten}, ...] chronological.
    """
    import datetime
    since = datetime.date.today()
    for _ in range(months - 1):
        since = (since.replace(day=1) - datetime.timedelta(days=1))
    since = since.replace(day=1)

    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT auction_date, tenor_label, cutoff_yield_pct
            FROM primary_yield_snapshots
            WHERE cutoff_yield_pct IS NOT NULL
              AND tenor_label IN ('91D', '2Y', '10Y')
              AND auction_date >= :since
            ORDER BY auction_date
        """), {"since": str(since)}).fetchall()

        # forward-fill latest yield per tenor as the date advances
        last = {"91D": None, "2Y": None, "10Y": None}
        by_date: dict = {}
        for r in rows:
            d = str(r._mapping["auction_date"])
            last[r._mapping["tenor_label"]] = r._mapping["cutoff_yield_pct"]
            by_date[d] = {
                "date": d,
                "y_91d": last["91D"],
                "y_2y":  last["2Y"],
                "y_10y": last["10Y"],
            }

        result = []
        for d in sorted(by_date):
            row = by_date[d]
            y91, y2, y10 = row["y_91d"], row["y_2y"], row["y_10y"]
            row["two_ten"]   = round(y10 - y2, 4) if (y10 is not None and y2 is not None) else None
            row["short_ten"] = round(y10 - y91, 4) if (y10 is not None and y91 is not None) else None
            result.append(row)
        return result
    finally:
        session.close()


@router.get("/secondary")
def get_secondary_curve():
    """Secondary market yield curve — latest GSOM MTM snapshot per ISIN."""
    import datetime
    today = datetime.date.today()
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT DISTINCT ON (m.isin)
                   m.isin, m.settlement_date, m.market_yield_pct,
                   m.outstanding_bdt_mill,
                   s.security_name_norm, s.security_type,
                   s.maturity_date, s.issue_date
            FROM mtm_snapshots m
            LEFT JOIN securities s ON m.isin = s.isin
            WHERE m.market_yield_pct IS NOT NULL
            ORDER BY m.isin, m.settlement_date DESC
        """)).fetchall()
        result = []
        for r in rows:
            d = dict(r._mapping)
            mat = d.get("maturity_date")
            if mat is None:
                continue
            rem_years = (mat - today).days / 365.25
            if rem_years < 0:
                continue
            d["remaining_years"] = round(rem_years, 3)
            result.append(d)
        return sorted(result, key=lambda x: x["remaining_years"])
    finally:
        session.close()
