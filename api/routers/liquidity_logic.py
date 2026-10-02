"""The liquidity ladder: OMO flow, government inflows and auction outflows,
over any date range, with a complete per-day breakdown.

FOUR LEGS, NOT TWO. Every OMO tranche touches liquidity twice:

  on its TRANSACTION date, entering BB's book
    * a new INJECTION (repo/AR/IBLF/SLF/...) -> BB lends cash  -> market IN
    * a new ABSORPTION (SDF)                 -> banks park cash -> market OUT

  on its MATURITY date, leaving BB's book -- the OPPOSITE sign
    * an INJECTION maturing  -> the bank repays BB      -> market OUT
    * an ABSORPTION maturing -> BB returns the deposit  -> market IN

The first version of this module had only the maturity legs, because it began
life as a forward forecast where fresh deals are unknowable -- BB has not acted
yet. That premise is sound forward and wrong backwards: for a past day the deals
are published facts. Measured over 1-15 Sep 2026 the maturity-only net was wrong
on 11 of 15 days, overstating one drain by 13,401 crore and FLIPPING THE SIGN on
09-Sep, where the page advised a "cheap borrowing window" on a net drain day.

So both legs are now signed off one function, `_stock_sign`, instead of two
hand-written sign tables that can disagree. That makes this identity true by
construction rather than by coincidence:

    net OMO flow(D) = net_outstanding(D) - net_outstanding(D-1)

a fresh deal ENTERS the outstanding stock, a maturing one LEAVES it. The tests
assert it over every seeded day; it cannot pass if a leg is dropped, which is
exactly how the bug got in.

A consequence worth knowing: an unrecognised `direction` is treated as an
ABSORPTION, because that is how it signs in the stock. Counting it as a drain on
BOTH legs would be "conservative" but would invent a permanent liquidity hole
that grows with every such row -- fabricated data, which is worse than a
consistent guess. validate.py is where an unknown instrument gets flagged.

Rows with `accepted_bdt_crore = 0` are BB's maturity-only lines (db.py: "Rows
with accepted_bdt_crore=0 are maturity-only lines") where maturity_date ==
transaction_date. Excluded everywhere: counting them alongside the tranches they
describe double-books the roll-off.

Self-contained: the deployed API is rooted at api/ and cannot import anything
above it.
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

# Coverage of the FRESH-DEAL leg for a given day.
COMPLETE = "complete"                          # BB has published the operations
AWAITING = "awaiting_publication"              # a past day; the press release will arrive
FUTURE = "future"                              # BB cannot have acted yet
NO_DATA = "no_data"                            # before OMO records begin


def _stock_sign(direction: Optional[str]) -> float:
    """How a live tranche signs in BB's net provision to the market.

    One source of truth for both legs. An injection adds to what BB has lent the
    market; anything else subtracts. See the module docstring on why an unknown
    direction lands on the absorption side.
    """
    return 1.0 if direction == INJECTION else -1.0


def deal_effect(direction: Optional[str]) -> str:
    """What a NEW operation does on its transaction date."""
    return INFLOW if _stock_sign(direction) > 0 else OUTFLOW


def liquidity_effect(direction: Optional[str]) -> str:
    """What a MATURING tranche does -- the opposite of the deal leg."""
    return INFLOW if _stock_sign(direction) < 0 else OUTFLOW


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


# Maturing on each day in the window.
_OMO_MATURING = """
    SELECT maturity_date AS d, instrument, direction,
           SUM(accepted_bdt_crore) AS crore
    FROM omo_transactions
    WHERE accepted_bdt_crore > 0
      AND maturity_date BETWEEN :f AND :t
    GROUP BY maturity_date, instrument, direction"""

# Dealt on each day in the window -- the leg that was missing.
_OMO_DEALT = """
    SELECT transaction_date AS d, instrument, direction,
           SUM(accepted_bdt_crore) AS crore
    FROM omo_transactions
    WHERE accepted_bdt_crore > 0
      AND transaction_date BETWEEN :f AND :t
    GROUP BY transaction_date, instrument, direction"""

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
    omo_from, omo_dealt_to, omo_to = session.execute(text(
        "SELECT MIN(transaction_date), MAX(transaction_date), MAX(maturity_date) "
        "FROM omo_transactions WHERE accepted_bdt_crore > 0")).fetchone()
    auction_to = session.execute(text(
        "SELECT MAX(settlement_date) FROM auction_events")).scalar()
    flows_from, flows_to = session.execute(text(
        "SELECT MIN(flow_date), MAX(flow_date) FROM daily_net_flow")).fetchone()
    return {
        "omo_data_from": str(_d(omo_from)) if omo_from else None,
        # The last day BB's operations are published. After this the fresh-deal
        # leg is UNKNOWN, so a day's net is roll-off only -- not a liquidity net.
        "omo_dealt_to": str(_d(omo_dealt_to)) if omo_dealt_to else None,
        "omo_data_to": str(_d(omo_to)) if omo_to else None,
        "auction_horizon": str(_d(auction_to)) if auction_to else None,
        "flows_data_from": str(_d(flows_from)) if flows_from else None,
        "flows_data_to": str(_d(flows_to)) if flows_to else None,
    }


def _coverage(day: datetime.date, omo_from: Optional[datetime.date],
              dealt_to: Optional[datetime.date], today: datetime.date) -> str:
    """Whether the fresh-deal leg is known for this day.

    `awaiting_publication` and `future` are deliberately distinct: the first
    resolves itself when BB publishes, the second never will.
    """
    if omo_from is None or day < omo_from:
        return NO_DATA
    if dealt_to is not None and day <= dealt_to:
        return COMPLETE
    return AWAITING if day <= today else FUTURE


def _bucket_items(rows, effect_fn) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for r in rows:
        out.setdefault(str(_d(r.d)), []).append({
            "instrument": r.instrument,
            "direction": r.direction,
            "liquidity_effect": effect_fn(r.direction),
            "crore": round(float(r.crore or 0), 2),
        })
    return out


def net_outstanding(session, on: datetime.date) -> float:
    """BB's signed net provision to the market as of `on`.

    The stock the daily flow must reconcile to: a tranche counts while
    transaction_date <= on < maturity_date. Exposed so the UI and the tests can
    check the flow against it rather than trusting the flow alone.
    """
    rows = session.execute(text(
        "SELECT direction, SUM(accepted_bdt_crore) AS crore FROM omo_transactions "
        "WHERE accepted_bdt_crore > 0 AND transaction_date <= :on AND maturity_date > :on "
        "GROUP BY direction"), {"on": str(on)}).fetchall()
    return round(sum(_stock_sign(r.direction) * float(r.crore or 0) for r in rows), 2)


def liquidity_ladder(session, days: Optional[int] = None,
                     date_from: Optional[str] = None,
                     date_to: Optional[str] = None,
                     today: datetime.date = None) -> dict:
    """Day-by-day liquidity over the window, in BDT crore.

    Past days carry the complete OMO flow (deals + maturities). Days BB has not
    published are roll-off only and say so in `omo_coverage` -- nothing here
    models what BB will do, because those operations react to this ladder.
    """
    f, t = resolve_window(days, date_from, date_to, today)
    today = today or datetime.date.today()
    p = {"f": str(f), "t": str(t)}

    maturing = _bucket_items(session.execute(text(_OMO_MATURING), p).fetchall(),
                             liquidity_effect)
    dealt = _bucket_items(session.execute(text(_OMO_DEALT), p).fetchall(), deal_effect)
    flows = {str(_d(r.d)): r for r in session.execute(text(_FLOWS_IN_WINDOW), p).fetchall()}

    h = _horizons(session)
    omo_from, dealt_to = _d(h["omo_data_from"]), _d(h["omo_dealt_to"])

    out: List[dict] = []
    cum = 0.0
    d = f
    while d <= t:
        key = str(d)
        mat = sorted(maturing.get(key, []), key=lambda i: -i["crore"])
        new = sorted(dealt.get(key, []), key=lambda i: -i["crore"])
        coverage = _coverage(d, omo_from, dealt_to, today)

        mat_in = sum(i["crore"] for i in mat if i["liquidity_effect"] == INFLOW)
        mat_out = sum(i["crore"] for i in mat if i["liquidity_effect"] == OUTFLOW)
        new_in = sum(i["crore"] for i in new if i["liquidity_effect"] == INFLOW)
        new_out = sum(i["crore"] for i in new if i["liquidity_effect"] == OUTFLOW)

        roll_net = mat_in - mat_out
        omo_net = roll_net + new_in - new_out

        row = flows.get(key)
        govt_inflow = (((row.coupon_inflow_bdt_mill or 0) +
                        (row.principal_inflow_bdt_mill or 0)) / MILL_TO_CRORE) if row else 0.0
        auction_out = (((row.auction_outflow_confirmed_mill or
                         row.auction_outflow_planned_mill or 0)) / MILL_TO_CRORE) if row else 0.0
        net = omo_net + govt_inflow - auction_out
        cum += net
        out.append({
            "date": key,
            "weekday": d.strftime("%a"),
            # maturity legs -- names kept, meaning unchanged
            "omo_return_crore": round(mat_in, 2),
            "omo_repay_crore": round(mat_out, 2),
            # the legs that were missing
            "omo_new_inflow_crore": round(new_in, 2),
            "omo_new_outflow_crore": round(new_out, 2),
            "omo_roll_net_crore": round(roll_net, 2),
            "omo_net_crore": round(omo_net, 2),
            "omo_coverage": coverage,
            "govt_inflow_crore": round(govt_inflow, 2),
            "auction_out_crore": round(auction_out, 2),
            "net_crore": round(net, 2),
            "cum_net_crore": round(cum, 2),
            "omo_items": mat,          # maturing (kept for existing callers)
            "omo_new_items": new,      # dealt
            "flows_confirmed": bool(row.data_complete) if row is not None else False,
            "omo_known": coverage != NO_DATA,
            # True only when BOTH legs are known -- the net is a liquidity net
            # rather than a roll-off figure.
            "omo_complete": coverage == COMPLETE,
            "is_past": d < today,
        })
        d += datetime.timedelta(days=1)

    return {"as_of": str(today), "from": str(f), "to": str(t),
            "unit": "BDT crore", "days": out, **h}


def day_detail(session, date: str) -> dict:
    """One day's OMO -- dealt and maturing -- plus the liquidity lines in the
    order cash moves.

    Returned as its own block so it can be bolted onto the existing drilldown
    response without touching a single field that is already there.
    """
    day = _d(date)
    rows = session.execute(text("""
        SELECT instrument, direction, tenor_label, MIN(rate_pct) AS rate_pct,
               SUM(accepted_bdt_crore) AS crore, MIN(transaction_date) AS from_date,
               'MATURING' AS leg
        FROM omo_transactions
        WHERE accepted_bdt_crore > 0 AND maturity_date = :d
        GROUP BY instrument, direction, tenor_label
        UNION ALL
        SELECT instrument, direction, tenor_label, MIN(rate_pct) AS rate_pct,
               SUM(accepted_bdt_crore) AS crore, MIN(maturity_date) AS from_date,
               'DEALT' AS leg
        FROM omo_transactions
        WHERE accepted_bdt_crore > 0 AND transaction_date = :d
        GROUP BY instrument, direction, tenor_label"""), {"d": str(day)}).fetchall()

    omo: List[dict] = []
    by_instrument: Dict[str, dict] = {}
    for r in rows:
        dealt = r.leg == "DEALT"
        eff = deal_effect(r.direction) if dealt else liquidity_effect(r.direction)
        crore = round(float(r.crore or 0), 2)
        omo.append({
            "instrument": r.instrument, "direction": r.direction,
            "leg": r.leg, "liquidity_effect": eff, "tenor_label": r.tenor_label,
            "rate_pct": r.rate_pct, "crore": crore,
            # for a maturing tranche this is when it was dealt; for a fresh deal
            # it is when it will mature
            "transacted_from": str(_d(r.from_date)) if r.from_date else None,
        })
        key = f"{r.instrument}|{r.leg}"
        slot = by_instrument.setdefault(key, {
            "instrument": r.instrument, "leg": r.leg, "liquidity_effect": eff, "crore": 0.0})
        slot["crore"] = round(slot["crore"] + crore, 2)

    def tot(leg, eff):
        return round(sum(x["crore"] for x in omo if x["leg"] == leg
                         and x["liquidity_effect"] == eff), 2)

    mat_in, mat_out = tot("MATURING", INFLOW), tot("MATURING", OUTFLOW)
    new_in, new_out = tot("DEALT", INFLOW), tot("DEALT", OUTFLOW)
    roll_net = round(mat_in - mat_out, 2)
    omo_net = round(roll_net + new_in - new_out, 2)

    flow = session.execute(text("""
        SELECT coupon_inflow_bdt_mill, principal_inflow_bdt_mill,
               auction_outflow_confirmed_mill, auction_outflow_planned_mill
        FROM daily_net_flow WHERE flow_date = :d"""), {"d": str(day)}).fetchone()
    coupon = (flow.coupon_inflow_bdt_mill or 0) / MILL_TO_CRORE if flow else 0.0
    principal = (flow.principal_inflow_bdt_mill or 0) / MILL_TO_CRORE if flow else 0.0
    auction_out = (((flow.auction_outflow_confirmed_mill or
                     flow.auction_outflow_planned_mill or 0)) / MILL_TO_CRORE) if flow else 0.0

    h = _horizons(session)
    omo_from, dealt_to = _d(h["omo_data_from"]), _d(h["omo_dealt_to"])
    auction_to = _d(h["auction_horizon"])
    coverage = _coverage(day, omo_from, dealt_to, datetime.date.today())

    govt_inflow = round(coupon + principal, 2)
    total = round(omo_net + govt_inflow - auction_out, 2)
    omo.sort(key=lambda x: (x["leg"], x["liquidity_effect"], -x["crore"]))

    return {
        "date": str(day),
        "omo": omo,
        "omo_by_instrument": sorted(by_instrument.values(), key=lambda x: -x["crore"]),
        "liquidity": {
            "unit": "BDT crore",
            "omo_new_inflow_crore": new_in,
            "omo_new_outflow_crore": new_out,
            "omo_inflow_crore": mat_in,
            "omo_outflow_crore": mat_out,
            "omo_roll_net_crore": roll_net,
            "omo_net_crore": omo_net,
            "coupon_inflow_crore": round(coupon, 2),
            "principal_inflow_crore": round(principal, 2),
            "govt_inflow_crore": govt_inflow,
            "auction_outflow_crore": round(auction_out, 2),
            "auction_net_crore": round(-auction_out, 2),
            "total_net_crore": total,
            "omo_coverage": coverage,
            "omo_known": coverage != NO_DATA,
            "omo_complete": coverage == COMPLETE,
            "auction_known": bool(auction_to and day <= auction_to),
        },
        **h,
    }


def omo_maturity_ladder(session, days: Optional[int] = None,
                        date_from: Optional[str] = None,
                        date_to: Optional[str] = None,
                        today: datetime.date = None) -> dict:
    """What rolls off on each day and what BB dealt, by instrument, with the
    roll-off net, the true OMO flow, and a cumulative anchored at the window's
    first row.

    Both nets are reported because they answer different questions: the roll-off
    is the funding cliff, the flow is what actually happened to liquidity. The
    old single "net" was the roll-off presented as the flow.
    """
    today = today or datetime.date.today()
    if days is not None and not (date_from or date_to):
        # Forward-looking by default: today through today+days, INCLUDING today,
        # because a tranche maturing today is still the desk's problem today.
        if not 1 <= int(days) <= 400:
            raise ValueError(f"days must be 1..400, got {days}")
        f, t = today, today + datetime.timedelta(days=int(days))
    else:
        f, t = resolve_window(None, date_from, date_to, today)

    p = {"f": str(f), "t": str(t)}
    maturing = _bucket_items(session.execute(text(_OMO_MATURING), p).fetchall(),
                             liquidity_effect)
    dealt = _bucket_items(session.execute(text(_OMO_DEALT), p).fetchall(), deal_effect)

    h = _horizons(session)
    omo_from, dealt_to = _d(h["omo_data_from"]), _d(h["omo_dealt_to"])

    out: List[dict] = []
    cum = 0.0
    roll_totals: Dict[str, float] = {}
    new_totals: Dict[str, float] = {}
    for key in sorted(set(maturing) | set(dealt)):
        mat = sorted(maturing.get(key, []), key=lambda i: -i["crore"])
        new = sorted(dealt.get(key, []), key=lambda i: -i["crore"])
        mat_in = round(sum(i["crore"] for i in mat if i["liquidity_effect"] == INFLOW), 2)
        mat_out = round(sum(i["crore"] for i in mat if i["liquidity_effect"] == OUTFLOW), 2)
        new_in = round(sum(i["crore"] for i in new if i["liquidity_effect"] == INFLOW), 2)
        new_out = round(sum(i["crore"] for i in new if i["liquidity_effect"] == OUTFLOW), 2)
        roll_net = round(mat_in - mat_out, 2)
        omo_net = round(roll_net + new_in - new_out, 2)
        cum = round(cum + omo_net, 2)
        for i in mat:
            roll_totals[i["instrument"]] = round(
                roll_totals.get(i["instrument"], 0.0) + i["crore"], 2)
        for i in new:
            new_totals[i["instrument"]] = round(
                new_totals.get(i["instrument"], 0.0) + i["crore"], 2)
        out.append({
            "date": key, "weekday": _d(key).strftime("%a"),
            "inflow_crore": mat_in, "outflow_crore": mat_out,
            "new_inflow_crore": new_in, "new_outflow_crore": new_out,
            "roll_net_crore": roll_net,
            "net_crore": omo_net,
            "cum_net_crore": cum,
            "by_instrument": {i["instrument"]: i["crore"] for i in mat},
            "new_by_instrument": {i["instrument"]: i["crore"] for i in new},
            "items": mat, "new_items": new,
            "omo_coverage": _coverage(_d(key), omo_from, dealt_to, today),
            "is_past": _d(key) < today,
        })

    instruments = sorted(set(roll_totals) | set(new_totals),
                         key=lambda k: -(roll_totals.get(k, 0) + new_totals.get(k, 0)))
    return {
        "as_of": str(today), "from": str(f), "to": str(t), "unit": "BDT crore",
        "days": out,
        "instruments": instruments,
        "instrument_totals": roll_totals,
        "new_instrument_totals": new_totals,
        "total_inflow_crore": round(sum(d["inflow_crore"] for d in out), 2),
        "total_outflow_crore": round(sum(d["outflow_crore"] for d in out), 2),
        "total_new_inflow_crore": round(sum(d["new_inflow_crore"] for d in out), 2),
        "total_new_outflow_crore": round(sum(d["new_outflow_crore"] for d in out), 2),
        "total_roll_net_crore": round(sum(d["roll_net_crore"] for d in out), 2),
        "total_net_crore": round(sum(d["net_crore"] for d in out), 2),
        **h,
    }
