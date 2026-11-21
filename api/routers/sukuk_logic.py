"""BGIS Sukuk for the Islamic page: what is outstanding, what BB posted, what pays next.

api/ deploys rooted at api/ and cannot import engines/, so the profit schedule
below is a deliberate copy of engines/aggregation.sukuk_cash_flows - without the
working-day roll (the calendar lives outside api/), so dates here are the SCHEDULED
dates and the page says cash settles on the next working day. A test asserts the
two agree on every amount, so they change together or the build fails.

The same rule applies as in the ladder: profit is derived only where BB states a
half-yearly frequency. "On Maturity" issues show principal only; an invented profit
figure is the one error this project will not make.
"""
import calendar
import datetime
from typing import Dict, List, Optional

from sqlalchemy import text


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


def add_months(d: datetime.date, n: int) -> datetime.date:
    """Same day n months on, clamped to the month's end (31-Aug + 6m = 28-Feb)."""
    m = d.month - 1 + n
    y, mo = d.year + m // 12, m % 12 + 1
    return datetime.date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def is_half_yearly(freq) -> bool:
    f = (freq or "").lower().replace("-", " ").replace("_", " ")
    return "half" in f and "year" in f or "semi" in f


def scheduled_payments(issue: Dict, date_from: datetime.date, date_to: datetime.date) -> List[Dict]:
    """Principal at maturity; profit on each 6-month anniversary if half-yearly."""
    crore = float(issue.get("outstanding_crore") or 0.0)
    mat, iss = _d(issue.get("maturity_date")), _d(issue.get("issue_date"))
    if not crore or not mat or not iss:
        return []
    out = []
    if date_from <= mat <= date_to:
        out.append({"date": mat, "kind": "PRINCIPAL", "amount_crore": round(crore, 4)})
    rate = issue.get("profit_rate_pct")
    if rate is not None and is_half_yearly(issue.get("profit_frequency")):
        k = 1
        while True:
            d = add_months(iss, 6 * k)
            if d > mat:
                break
            if date_from <= d <= date_to:
                out.append({"date": d, "kind": "PROFIT",
                            "amount_crore": round(crore * float(rate) / 100.0 / 2.0, 4)})
            k += 1
    return out


def announced_auctions(session, issues: list, today: datetime.date) -> list:
    """Sukuk auctions BB has ANNOUNCED in a press release, newest first, with where each stands:
    ANNOUNCED (auction not yet held), HELD (auction day passed, BB's sukuk page has not listed the issue yet),
    LISTED (BB's outstanding table now holds that maturity). [] when the table does not exist yet or is unreadable -
    the page then simply shows no announcement, never a guessed one."""
    try:
        rows = session.execute(text(
            "SELECT source_serial, source_url, name, series_no, auction_date, issue_date, maturity_date, tenor_days, "
            "face_crore, rent_crore, rate_pct FROM sukuk_auctions ORDER BY auction_date DESC LIMIT 12")).fetchall()
    except Exception:
        session.rollback()
        return []
    listed = {_d(i["maturity_date"]) if not isinstance(i["maturity_date"], str) else datetime.date.fromisoformat(i["maturity_date"][:10])
              for i in issues}
    out = []
    for r in rows:
        a = dict(r._mapping)
        for k in ("auction_date", "issue_date", "maturity_date"):
            a[k] = _d(a[k])
        a["status"] = ("LISTED" if a["maturity_date"] in listed
                       else "ANNOUNCED" if a["auction_date"] >= today else "HELD")
        for k in ("auction_date", "issue_date", "maturity_date"):
            a[k] = str(a[k])
        out.append(a)
    return out


def sukuk_block(session, today: datetime.date = None, doc_limit: int = 15,
                horizon_days: int = 365) -> Dict:
    today = today or datetime.date.today()
    rows = session.execute(text(
        "SELECT isin, project, tenor_label, contract, issue_date, maturity_date, "
        "profit_rate_pct, profit_frequency, tradability, outstanding_crore, last_seen_utc "
        "FROM sukuk_outstanding ORDER BY maturity_date, isin")).fetchall()
    issues = []
    for r in rows:
        m = dict(r._mapping)
        m["issue_date"], m["maturity_date"] = _d(m["issue_date"]), _d(m["maturity_date"])
        issues.append(m)

    horizon = today + datetime.timedelta(days=horizon_days)
    upcoming = []
    for i in issues:
        for p in scheduled_payments(i, today, horizon):
            upcoming.append({"isin": i["isin"], "project": i["project"], **p,
                             "date": str(p["date"]),
                             "profit_derived": p["kind"] == "PROFIT"})
    upcoming.sort(key=lambda p: (p["date"], p["isin"]))

    docs = [dict(r._mapping) for r in session.execute(text(
        "SELECT doc_key, kind, doc_date, ref, title, url FROM sukuk_documents "
        "ORDER BY doc_date DESC, doc_key DESC LIMIT :n"), {"n": doc_limit}).fetchall()]
    for d in docs:
        d["doc_date"] = str(_d(d["doc_date"]) or "")
    for i in issues:
        i["issue_date"], i["maturity_date"] = str(i["issue_date"]), str(i["maturity_date"])
        i["last_seen_utc"] = str(i["last_seen_utc"]) if i.get("last_seen_utc") else None
        i["profit_derivable"] = is_half_yearly(i.get("profit_frequency")) and i.get("profit_rate_pct") is not None

    return {
        "announced": announced_auctions(session, issues, today),
        "as_of": str(today),
        "count": len(issues),
        "total_outstanding_crore": round(sum(float(i["outstanding_crore"] or 0) for i in issues), 2),
        "outstanding": issues,
        "upcoming": upcoming,
        "documents": docs,
        "note": ("Scheduled dates; cash settles on the next working day. Profit is derived only "
                 "for issues BB lists as half-yearly (outstanding x rate / 2); issues paying "
                 "profit 'on Maturity' show principal only."),
    }
