"""The liquidity ladder: OMO roll-off, government inflows and auction outflows,
over any date range, with a complete per-day breakdown.

THE SIGN TRAP, which everything here turns on:

  `omo_transactions.direction` describes the ORIGINAL operation. At MATURITY the
  liquidity effect is the opposite:

    * an ABSORPTION maturing (SDF) -> BB returns the deposit -> cash flows IN
    * an INJECTION maturing (repo/AR/IBLF/SLF/...) -> the bank repays BB -> OUT

  So "how much repo is maturing" is a DRAIN, not an injection, even though repo
  is an injection instrument. Getting this backwards inverts the entire ladder,
  so every row carries an explicit `liquidity_effect` and callers are never
  asked to re-derive it from `direction`.

Rows with `accepted_bdt_crore = 0` are BB's maturity-only lines (see db.py:
"Rows with accepted_bdt_crore=0 are maturity-only lines") where
maturity_date == transaction_date. They are excluded everywhere here: counting
them alongside the tranches they describe double-books the roll-off.

Basis: the roll-off is DERIVED from live tranches. BB separately prints
`maturity_bdt_crore`, and the weekly deep audit reconciles the two ("OMO ledger
vs BB's printed maturities"), so this inherits an audited basis.

Self-contained by necessity: the deployed API is rooted at api/ and cannot
import anything above it. tests/test_schedule.py AST-scans for that.
"""
import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

MILL_TO_CRORE = 10.0          # BB publishes crore; the ladder tables store million
MAX_RANGE_DAYS = 800          # a bounded window keeps a stray query from scanning everything

ABSORPTION = "ABSORPTION"
INJECTION = "INJECTION"
INFLOW = "INFLOW"
OUTFLOW = "OUTFLOW"


def liquidity_effect(direction: Optional[str]) -> str:
    """What a maturing tranche does to market liquidity. See THE SIGN TRAP."""
    return INFLOW if direction == ABSORPTION else OUTFLOW


def _d(v):
    if v is None or hasattr(v, "year"):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


def resolve_window(days: Optional[int] = None,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   today: datetime.date = None) -> Tuple[datetime.date, datetime.date]:
    """Explicit range when given, else the legacy forward `days` horizon.

    The forward default reproduces the original behaviour exactly -- tomorrow
    through today+days -- so existing callers and cached responses do not shift
    by a day when an explicit range becomes available.
    """
    today = today or datetime.date.today()
    if date_from or date_to:
        f = _d(date_from) if date_from else today + datetime.timedelta(days=1)
        t = _d(date_to) if date_to else f + datetime.timedelta(days=27)
        if t < f:
            raise ValueError(f"date_to {t} is before date_from {f}")
        if (t - f).days > MAX_RANGE_DAYS:
            raise ValueError(f"range {f}..{t} exceeds {MAX_RANGE_DAYS} days")
        return f, t
    n = 28 if days is None else int(days)
    if not 1 <= n <= 400:
        raise ValueError(f"days must be 1..400, got {n}")
    return today + datetime.timedelta(days=1), today + datetime.timedelta(days=n)


_OMO_IN_WINDOW = """
    SELECT maturity_date AS d, instrument, direction,
           SUM(accepted_bdt_crore) AS crore
    FROM omo_transactions
    WHERE accepted_bdt_crore > 0
      AND maturity_date BETWEEN :f AND :t
    GROUP BY maturity_date, instrument, direction"""

_FLOWS_IN_WINDOW = """
    SELECT flow_date AS d, coupon_inflow_bdt_mill, principal_inflow_bdt_mill,
           auction_outflow_confirmed_mill, auction_outflow_planned_mill, data_complete
    FROM daily_net_flow
    WHERE flow_date BETWEEN :f AND :t"""


def _horizons(session) -> dict:
    """Where each source's knowledge starts and stops.

    Returned as data, not baked into the UI as a constant, because these move
    every time BB publishes and a hardcoded date would quietly rot.
    """
    omo_from, omo_to = session.execute(text(
        "SELECT MIN(transaction_date), MAX(maturity_date) FROM omo_transactions "
        "WHERE accepted_bdt_crore > 0")).fetchone()
    auction_to = session.execute(text(
        "SELECT MAX(settlement_date) FROM auction_events")).scalar()
    flows_from, flows_to = session.execute(text(
        "SELECT MIN(flow_date), MAX(flow_date) FROM daily_net_flow")).fetchone()
    return {
        "omo_data_from": str(_d(omo_from)) if omo_from else None,
        "omo_data_to": str(_d(omo_to)) if omo_to else None,
        "auction_horizon": str(_d(auction_to)) if auction_to else None,
        "flows_data_from": str(_d(flows_from)) if flows_from else None,
        "flows_data_to": str(_d(flows_to)) if flows_to else None,
    }


def liquidity_ladder(session, days: Optional[int] = None,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None,
                     today: datetime.date = None) -> dict:
    """Day-by-day known liquidity over the window, in BDT crore.

    Every figure is a contracted, dated flow. Nothing here models future BB
    operations -- those react to this ladder, and inventing them would turn a
    statement of fact into a guess.
    """
    f, t = resolve_window(days, date_from, date_to, today)
    today = today or datetime.date.today()
    p = {"f": str(f), "t": str(t)}

    omo_by_day: Dict[str, List[dict]] = {}
    for r in session.execute(text(_OMO_IN_WINDOW), p).fetchall():
        omo_by_day.setdefault(str(_d(r.d)), []).append({
            "instrument": r.instrument,
            "direction": r.direction,
            "liquidity_effect": liquidity_effect(r.direction),
            "crore": round(float(r.crore or 0), 2),
        })
    flows = {str(_d(r.d)): r for r in session.execute(text(_FLOWS_IN_WINDOW), p).fetchall()}

    h = _horizons(session)
    omo_from = _d(h["omo_data_from"])

    out: List[dict] = []
    cum = 0.0
    d = f
    while d <= t:
        key = str(d)
        items = sorted(omo_by_day.get(key, []), key=lambda i: -i["crore"])
        omo_return = sum(i["crore"] for i in items if i["liquidity_effect"] == INFLOW)
        omo_repay = sum(i["crore"] for i in items if i["liquidity_effect"] == OUTFLOW)
        row = flows.get(key)
        govt_inflow = (((row.coupon_inflow_bdt_mill or 0) +
                        (row.principal_inflow_bdt_mill or 0)) / MILL_TO_CRORE) if row else 0.0
        auction_out = (((row.auction_outflow_confirmed_mill or
                         row.auction_outflow_planned_mill or 0)) / MILL_TO_CRORE) if row else 0.0
        net = omo_return - omo_repay + govt_inflow - auction_out
        cum += net
        out.append({
            "date": key,
            "weekday": d.strftime("%a"),
            "omo_return_crore": round(omo_return, 2),
            "omo_repay_crore": round(omo_repay, 2),
            "omo_net_crore": round(omo_return - omo_repay, 2),
            "govt_inflow_crore": round(govt_inflow, 2),
            "auction_out_crore": round(auction_out, 2),
            "net_crore": round(net, 2),
            "cum_net_crore": round(cum, 2),
            "omo_items": items,
            "flows_confirmed": bool(row.data_complete) if row is not None else False,
            # A day outside a source's coverage has NO data, which is a different
            # statement from "nothing happened". The UI must not print 0 for it.
            "omo_known": bool(omo_from and d >= omo_from),
            "is_past": d < today,
        })
        d += datetime.timedelta(days=1)

    return {"as_of": str(today), "from": str(f), "to": str(t),
            "unit": "BDT crore", "days": out, **h}


def day_detail(session, date: str) -> dict:
    """One day's OMO, netted, plus the liquidity lines in the order cash moves.

    Returned as its own block so it can be bolted onto the existing drilldown
    response without touching a single field that is already there.
    """
    day = _d(date)
    rows = session.execute(text("""
        SELECT instrument, direction, tenor_label, MIN(rate_pct) AS rate_pct,
               SUM(accepted_bdt_crore) AS crore, MIN(transaction_date) AS from_date
        FROM omo_transactions
        WHERE accepted_bdt_crore > 0 AND maturity_date = :d
        GROUP BY instrument, direction, tenor_label"""), {"d": str(day)}).fetchall()

    omo: List[dict] = []
    by_instrument: Dict[str, dict] = {}
    for r in rows:
        eff = liquidity_effect(r.direction)
        crore = round(float(r.crore or 0), 2)
        omo.append({
            "instrument": r.instrument, "direction": r.direction,
            "liquidity_effect": eff, "tenor_label": r.tenor_label,
            "rate_pct": r.rate_pct, "crore": crore,
            "transacted_from": str(_d(r.from_date)) if r.from_date else None,
        })
        slot = by_instrument.setdefault(r.instrument, {
            "instrument": r.instrument, "liquidity_effect": eff, "crore": 0.0})
        slot["crore"] = round(slot["crore"] + crore, 2)

    omo_in = round(sum(x["crore"] for x in omo if x["liquidity_effect"] == INFLOW), 2)
    omo_out = round(sum(x["crore"] for x in omo if x["liquidity_effect"] == OUTFLOW), 2)

    flow = session.execute(text("""
        SELECT coupon_inflow_bdt_mill, principal_inflow_bdt_mill,
               auction_outflow_confirmed_mill, auction_outflow_planned_mill
        FROM daily_net_flow WHERE flow_date = :d"""), {"d": str(day)}).fetchone()
    coupon = (flow.coupon_inflow_bdt_mill or 0) / MILL_TO_CRORE if flow else 0.0
    principal = (flow.principal_inflow_bdt_mill or 0) / MILL_TO_CRORE if flow else 0.0
    auction_out = (((flow.auction_outflow_confirmed_mill or
                     flow.auction_outflow_planned_mill or 0)) / MILL_TO_CRORE) if flow else 0.0

    h = _horizons(session)
    omo_from, auction_to = _d(h["omo_data_from"]), _d(h["auction_horizon"])

    govt_inflow = round(coupon + principal, 2)
    total = round(omo_in - omo_out + govt_inflow - auction_out, 2)
    omo.sort(key=lambda x: (x["liquidity_effect"], -x["crore"]))

    return {
        "date": str(day),
        "omo": omo,
        "omo_by_instrument": sorted(by_instrument.values(), key=lambda x: -x["crore"]),
        "liquidity": {
            "unit": "BDT crore",
            "omo_inflow_crore": omo_in,
            "omo_outflow_crore": omo_out,
            "omo_net_crore": round(omo_in - omo_out, 2),
            "coupon_inflow_crore": round(coupon, 2),
            "principal_inflow_crore": round(principal, 2),
            "govt_inflow_crore": govt_inflow,
            "auction_outflow_crore": round(auction_out, 2),
            "auction_net_crore": round(-auction_out, 2),
            "total_net_crore": total,
            "omo_known": bool(omo_from and day >= omo_from),
            "auction_known": bool(auction_to and day <= auction_to),
        },
        **h,
    }


def omo_maturity_ladder(session, days: Optional[int] = None,
                        date_from: Optional[str] = None,
                        date_to: Optional[str] = None,
                        today: datetime.date = None) -> dict:
    """What rolls off on each day, by instrument, with the day's net and a
    running cumulative anchored at the first day of the window.

    The cumulative starts at the window's first row on purpose: a running total
    carried in from data the caller cannot see is worse than none at all.
    """
    today = today or datetime.date.today()
    if days is not None and not (date_from or date_to):
        # Forward-looking by default: today through today+days, INCLUDING today,
        # because a tranche maturing today is still the desk's problem today.
        f, t = today, today + datetime.timedelta(days=int(days))
        if not 1 <= int(days) <= 400:
            raise ValueError(f"days must be 1..400, got {days}")
    else:
        f, t = resolve_window(None, date_from, date_to, today)

    rows = session.execute(text(_OMO_IN_WINDOW), {"f": str(f), "t": str(t)}).fetchall()
    by_day: Dict[str, List[dict]] = {}
    for r in rows:
        by_day.setdefault(str(_d(r.d)), []).append({
            "instrument": r.instrument, "direction": r.direction,
            "liquidity_effect": liquidity_effect(r.direction),
            "crore": round(float(r.crore or 0), 2),
        })

    h = _horizons(session)
    out: List[dict] = []
    cum = 0.0
    totals: Dict[str, float] = {}
    for key in sorted(by_day):
        items = sorted(by_day[key], key=lambda i: -i["crore"])
        inflow = round(sum(i["crore"] for i in items if i["liquidity_effect"] == INFLOW), 2)
        outflow = round(sum(i["crore"] for i in items if i["liquidity_effect"] == OUTFLOW), 2)
        net = round(inflow - outflow, 2)
        cum = round(cum + net, 2)
        for i in items:
            totals[i["instrument"]] = round(totals.get(i["instrument"], 0.0) + i["crore"], 2)
        out.append({
            "date": key, "weekday": _d(key).strftime("%a"),
            "inflow_crore": inflow, "outflow_crore": outflow,
            "net_crore": net, "cum_net_crore": cum,
            "by_instrument": {i["instrument"]: i["crore"] for i in items},
            "items": items,
            "is_past": _d(key) < today,
        })

    return {
        "as_of": str(today), "from": str(f), "to": str(t), "unit": "BDT crore",
        "days": out,
        "instruments": sorted(totals, key=lambda k: -totals[k]),
        "instrument_totals": totals,
        "total_inflow_crore": round(sum(d["inflow_crore"] for d in out), 2),
        "total_outflow_crore": round(sum(d["outflow_crore"] for d in out), 2),
        "total_net_crore": round(sum(d["net_crore"] for d in out), 2),
        **h,
    }
