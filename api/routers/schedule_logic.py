"""The forward schedule: what redeems, what pays coupon, what the desk must fund
— split by product and bucketed by month.

Why this is not read off daily_net_flow, the materialised ladder:

  * the ladder carries only aggregate coupon/principal inflow, with no product
    split at all, so it cannot say how much of a month is T-Bond versus T-Bill;
  * the ladder is maintained over a rolling window (2025-07..2027-11 as of
    Oct-2026) while coupon_events and maturity_events run out to 2045-08-27, so
    it cannot answer anything beyond next year either.

Both halves of the question live in the events, joined to securities for the
product — the same join /api/flows/drilldown already uses for a single date.

Two honesty rules are built in rather than left to the UI:

  * T-Bills are zero-coupon discount instruments. Their coupon figure is a real
    0.0, not missing data, and `coupon_products` names which products can carry
    one so a caller never has to guess.
  * BB publishes its auction calendar about a year ahead. Past the end of it
    there is no auction data, and 0 would read as "no auction" when the truth is
    "BB has not said yet" — a stale calendar presented as fact has misled this
    dashboard once already. Every month carries a status instead.

Amounts are BDT crore, the unit BB publishes and the desk speaks.
"""
import datetime
from typing import Dict, List, Tuple

from sqlalchemy import text

# CRORE_TO_MILLION and fiscal_year() are deliberately duplicated from the
# repo-root config.py, for the same reason api/db.py duplicates its driver
# guard: the deployed API is rooted at api/ and CANNOT import anything above
# it. Importing config here returns 500 ModuleNotFoundError in production while
# passing every local test, because locally the repo root is on sys.path.
# tests/test_schedule.py asserts both copies agree and that this module imports
# nothing from the repo root, so the two can never drift and nobody can
# re-break the deployment boundary without a red test.
CRORE_TO_MILLION = 10


def fiscal_year(d: datetime.date) -> str:
    """Bangladesh fiscal year, July-June. Mirrors config.fiscal_year()."""
    if d.month >= 7:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"

# OTHER is not padding. It catches a NULL security_type — an event whose ISIN has
# no securities row — and any product BB introduces later, so a new instrument
# shows up as an unlabelled bucket instead of silently dropping out of a total.
PRODUCTS = ("T_BOND", "T_BILL", "FRTB")
OTHER = "OTHER"
COUPON_PRODUCTS = ("T_BOND", "FRTB")
MAX_YEARS = 20


def _bucket(stype) -> str:
    return stype if stype in PRODUCTS else OTHER


def _empty() -> Dict[str, float]:
    return {k: 0.0 for k in PRODUCTS + (OTHER,)}


def _d(v):
    return datetime.date.fromisoformat(str(v)[:10]) if not hasattr(v, "year") else v


def window(years: int, today: datetime.date = None) -> Tuple[datetime.date, datetime.date]:
    """First of the current month, out to the day before the same month `years` on."""
    if not 1 <= years <= MAX_YEARS:
        raise ValueError(f"years must be 1..{MAX_YEARS}, got {years}")
    start = (today or datetime.date.today()).replace(day=1)
    return start, datetime.date(start.year + years, start.month, 1) - datetime.timedelta(days=1)


def _next_month(d: datetime.date) -> datetime.date:
    return (d.replace(day=28) + datetime.timedelta(days=7)).replace(day=1)


# Grouping is by DATE, not by month, and the month bucket is formed in Python.
# TO_CHAR is Postgres-only and strftime is SQLite-only; grouping on the date
# column works identically on both, and the row counts are small enough
# (~8,500 events over the full 20 years) that it costs nothing.
_MATURITIES = """
    SELECT m.payment_date AS d, s.security_type AS st, SUM(m.principal_bdt_mill) AS amt
    FROM maturity_events m LEFT JOIN securities s ON m.isin = s.isin
    WHERE m.payment_date BETWEEN :start AND :end GROUP BY m.payment_date, s.security_type"""
_COUPONS = """
    SELECT c.payment_date AS d, s.security_type AS st, SUM(c.amount_bdt_mill) AS amt
    FROM coupon_events c LEFT JOIN securities s ON c.isin = s.isin
    WHERE c.payment_date BETWEEN :start AND :end GROUP BY c.payment_date, s.security_type"""
# An auction row is PLANNED (BB's calendar target) or CONFIRMED (what BB
# accepted). Prefer accepted and fall back to offered — the precedence
# /drilldown and the ladder already use, so the three never disagree about the
# size of the same auction.
_AUCTIONS = """
    SELECT settlement_date AS d, security_type AS st,
           SUM(COALESCE(accepted_amount_bdt_mill, offered_amount_bdt_mill, 0)) AS amt
    FROM auction_events
    WHERE settlement_date BETWEEN :start AND :end GROUP BY settlement_date, security_type"""


def monthly_by_product(session, years: int = 2, today: datetime.date = None) -> dict:
    """Months from the start of this one, each split by product, plus FY subtotals."""
    start, end = window(years, today)
    p = {"start": str(start), "end": str(end)}

    acc: Dict[str, Dict[str, Dict[str, float]]] = {}
    for kind, sql in (("redemption", _MATURITIES), ("coupon", _COUPONS), ("auction", _AUCTIONS)):
        for row in session.execute(text(sql), p).fetchall():
            ym = _d(row.d).strftime("%Y-%m")
            m = acc.setdefault(ym, {"redemption": _empty(), "coupon": _empty(),
                                    "auction": _empty()})
            m[kind][_bucket(row.st)] += float(row.amt or 0) / CRORE_TO_MILLION

    cal_to = session.execute(text("SELECT MAX(settlement_date) FROM auction_events")).scalar()
    cal_to = _d(cal_to) if cal_to is not None else None

    months: List[dict] = []
    d = start
    while d <= end:
        ym = d.strftime("%Y-%m")
        raw = acc.get(ym) or {"redemption": _empty(), "coupon": _empty(), "auction": _empty()}
        month_end = _next_month(d) - datetime.timedelta(days=1)
        if cal_to is None or d > cal_to:
            status = "not_published"
        elif month_end > cal_to:
            status = "partial"
        else:
            status = "published"

        parts = {}
        for kind in ("redemption", "coupon", "auction"):
            total = round(sum(raw[kind].values()), 2)
            parts[kind] = {k: round(v, 2) for k, v in raw[kind].items()}
            parts[kind]["total"] = total
        parts["auction"]["status"] = status

        inflow = round(parts["redemption"]["total"] + parts["coupon"]["total"], 2)
        months.append({
            "month": ym, "fiscal_year": fiscal_year(d), **parts,
            "inflow_total": inflow,
            "net_borrowing": round(parts["auction"]["total"] - inflow, 2),
        })
        d = _next_month(d)

    fys = []
    for fy in sorted({m["fiscal_year"] for m in months}):
        mine = [m for m in months if m["fiscal_year"] == fy]
        sub = summarise(mine)
        sub["fiscal_year"] = fy
        fys.append(sub)

    # The longest bond on issue matures 2045-08-27, so a 20-year horizon ends in
    # a run of genuinely empty months. Naming the last dated flow lets the UI say
    # "nothing scheduled after this" instead of showing a dozen bare zero rows
    # that look like a data gap.
    dated = [m["month"] for m in months if m["inflow_total"] or m["auction"]["total"]]
    return {
        "from": str(start), "to": str(end), "years": years, "unit": "crore",
        "products": list(PRODUCTS) + [OTHER],
        "coupon_products": list(COUPON_PRODUCTS),
        "auction_calendar_to": str(cal_to) if cal_to else None,
        "last_month_with_flows": dated[-1] if dated else None,
        "months": months, "fy_subtotals": fys, "totals": summarise(months),
    }


def summarise(months: List[dict]) -> dict:
    """Add up a run of months, keeping the product split intact.

    net_borrowing is an inflow-minus-outflow figure, so over any span reaching
    past BB's auction calendar it nets full inflows against partial outflows.
    Summed over 20 years that reads as a trillion-crore surplus purely because
    only three months of auctions are published. The arithmetic is still
    reported — it is a true sum of what we hold — but `net_borrowing_comparable`
    says whether the two sides actually cover the same window, so nothing
    presents an artefact of missing data as a forecast.
    """
    out: Dict[str, Dict[str, float]] = {"redemption": _empty(), "coupon": _empty(),
                                        "auction": _empty()}
    for m in months:
        for kind in out:
            for k in PRODUCTS + (OTHER,):
                out[kind][k] += m[kind][k]
    res: dict = {}
    for kind, vals in out.items():
        total = round(sum(vals.values()), 2)
        res[kind] = {k: round(v, 2) for k, v in vals.items()}
        res[kind]["total"] = total
    res["inflow_total"] = round(res["redemption"]["total"] + res["coupon"]["total"], 2)
    res["net_borrowing"] = round(res["auction"]["total"] - res["inflow_total"], 2)
    published = sum(1 for m in months if m["auction"]["status"] != "not_published")
    res["months"] = len(months)
    res["auction_months_published"] = published
    res["net_borrowing_comparable"] = published == len(months)
    return res


_DETAIL = (
    ("REDEMPTION", """
        SELECT m.payment_date AS d, m.isin AS isin, s.security_name_norm AS nm,
               s.security_type AS st, m.principal_bdt_mill AS amt,
               NULL AS rate, m.scheduled_date AS sched
        FROM maturity_events m LEFT JOIN securities s ON m.isin = s.isin
        WHERE m.payment_date BETWEEN :start AND :end"""),
    ("COUPON", """
        SELECT c.payment_date AS d, c.isin AS isin, s.security_name_norm AS nm,
               s.security_type AS st, c.amount_bdt_mill AS amt,
               c.coupon_rate_used_pct AS rate, c.scheduled_date AS sched
        FROM coupon_events c LEFT JOIN securities s ON c.isin = s.isin
        WHERE c.payment_date BETWEEN :start AND :end"""),
)


def event_detail(session, years: int = 2, today: datetime.date = None) -> dict:
    """Every coupon and maturity behind monthly_by_product, one row each.

    This is what makes a month's figure auditable: the export ships it beside
    the monthly table, so any total can be traced to the securities paying it
    without walking /drilldown date by date. The scheduled date rides along
    because cash is dated to SETTLEMENT here, and the contractual due date is
    the thing a counterparty will quote back at you.
    """
    start, end = window(years, today)
    p = {"start": str(start), "end": str(end)}
    rows: List[dict] = []
    for kind, sql in _DETAIL:
        for r in session.execute(text(sql), p).fetchall():
            pay = _d(r.d)
            rows.append({
                "kind": kind, "month": pay.strftime("%Y-%m"), "payment_date": str(pay),
                "scheduled_date": str(_d(r.sched)) if r.sched else None,
                "product": _bucket(r.st), "isin": r.isin, "security": r.nm,
                "coupon_rate_pct": r.rate,
                "amount_crore": round(float(r.amt or 0) / CRORE_TO_MILLION, 2),
                "amount_mill": round(float(r.amt or 0), 2),
            })
    rows.sort(key=lambda x: (x["payment_date"], x["kind"], x["isin"] or ""))
    return {"from": str(start), "to": str(end), "years": years, "unit": "crore",
            "count": len(rows), "rows": rows}
