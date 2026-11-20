"""What is happening to liquidity, why, and what is coming - computed from BB's own data, every number traceable.

This is the layer the Liquidity Watch lacked: a list of news events says nothing about whether liquidity is loose or tight. Four questions, each
answered by CODE from data we hold, with the date of every input and an honest "unknown" where an input is missing:

  1. regime      - is the market in surplus, balanced or tight TODAY, and why (price first, then BB's own operations);
  2. drivers     - what moved liquidity this week: OMO lending, government securities, dollar operations;
  3. outlook     - the next working days: what government securities will settle against what matures, and which days drain;
  4. statements  - ranked, evidence-linked sentences a desk can act on.

Rules this module keeps (project rules, not choices):
  * ONE sign convention for flows: + adds cash to the banks, - drains it. "BB net lending" is a STOCK and is never called cash added.
  * An unknown is None, never 0. A day whose auction calendar BB has not published has an UNKNOWN securities net, not a zero one.
  * News-derived events are listed beside the numbers and NEVER change one (tests/test_news_api.py guards the ladder; the tests here guard this module).
  * The thresholds are named constants below and shown with every verdict, so the desk can see - and change - the rule it is being judged by.

api/ deploys rooted at api/: stdlib and sqlalchemy only; sibling logic modules are reused, nothing is re-derived.
"""
import datetime
import hashlib
import json
from typing import Dict, List, Optional

from sqlalchemy import text

try:
    from . import liquidity_logic as LL, news_logic as NL, omo_logic as OL
except ImportError:                       # imported as a top-level module (the tests load api/routers on sys.path)
    import liquidity_logic as LL
    import news_logic as NL
    import omo_logic as OL

# ── the rules, in the open (percentage points for rates, BDT crore for money) ────────────────────────────────────────
TIGHT_NEAR_SLF = 0.50            # call money within 50bp of the SLF ceiling: banks are borrowing at the top of the corridor
TIGHT_ABOVE_REPO = 0.25          # ...or 25bp above the repo rate
SURPLUS_NEAR_SDF = 0.50          # call money within 50bp of the SDF floor: banks have cash and nobody to lend it to
SURPLUS_BELOW_REPO = 0.25        # ...or 25bp below the repo rate
SEGMENTED_MIN_CRORE = 2000       # fresh BB lending AND SDF parking both at least this on one day: a two-speed market
LARGE_DAY_CRORE = 5000           # a single-day securities drain worth a line (the Forecast page's Desk Alert value)
SQUEEZE_CUM_CRORE = 10000        # a cumulative securities drain over the outlook window worth a line
CALL_MOVE_BP = 15                # a week-on-week call-money move worth a line
MOVER_MIN_CRORE = 1000           # an OMO facility whose stock moved this much in a week
HORIZON_WORKING_DAYS = 10
WEEK_DAYS = 7                    # "this week": the same window for the stock change, the settled securities cash and the dollar operations
NEWS_LOOKBACK_DAYS = 14

RULES = {
    "tight": f"call money >= SLF - {TIGHT_NEAR_SLF:.2f} or >= repo + {TIGHT_ABOVE_REPO:.2f}",
    "surplus": f"call money <= SDF + {SURPLUS_NEAR_SDF:.2f} or <= repo - {SURPLUS_BELOW_REPO:.2f}",
    "otherwise": "balanced",
}
TIGHT, BALANCED, SURPLUS, UNKNOWN = "TIGHT", "BALANCED", "SURPLUS", "UNKNOWN"
WORKING_WEEKDAYS = (6, 0, 1, 2, 3)     # Sunday-Thursday; Friday and Saturday are the weekend


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


def _num(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ── 1. regime ────────────────────────────────────────────────────────────────────────────────────────────────────
def regime(call: Optional[dict], corridor: Optional[dict], fresh: Optional[dict]) -> dict:
    """Surplus / balanced / tight, decided by PRICE (where overnight money trades inside BB's corridor) and explained by BB's operations.

    call     - {"rate", "date", "provisional"}                    overnight weighted-average rate
    corridor - {"repo", "slf", "sdf", "effective_date"}           the policy corridor in force
    fresh    - {"date", "injection_crore", "sdf_crore"}           what BB lent / banks parked on the last day BB published
    Any missing input makes the verdict UNKNOWN: it is never guessed from the others."""
    c = _num((call or {}).get("rate"))
    repo, slf, sdf = (_num((corridor or {}).get(k)) for k in ("repo", "slf", "sdf"))
    out = {"label": UNKNOWN, "rule": RULES, "why": [], "position_in_corridor_pct": None,
           "inputs": {"call": call, "corridor": corridor, "fresh": fresh}, "segmented": False}
    if c is None or None in (repo, slf, sdf):
        miss = [n for n, v in (("call money", c), ("repo", repo), ("SLF", slf), ("SDF", sdf)) if v is None]
        out["why"].append("cannot be judged: " + ", ".join(miss) + " not available")
        return out
    out["position_in_corridor_pct"] = round(100.0 * (c - sdf) / (slf - sdf), 1) if slf > sdf else None
    spread_repo = round(c - repo, 2)
    if c >= slf - TIGHT_NEAR_SLF or c >= repo + TIGHT_ABOVE_REPO:
        out["label"] = TIGHT
    elif c <= sdf + SURPLUS_NEAR_SDF or c <= repo - SURPLUS_BELOW_REPO:
        out["label"] = SURPLUS
    else:
        out["label"] = BALANCED
    out["why"].append(f"overnight call money {c:.2f}% is {abs(spread_repo):.2f} points {'above' if spread_repo > 0 else 'below'} the "
                      f"{repo:.2f}% repo rate (corridor {sdf:.2f}% - {slf:.2f}%)"
                      + (" [provisional: today's table is still running]" if (call or {}).get("provisional") else ""))
    if fresh:
        inj, park = _num(fresh.get("injection_crore")), _num(fresh.get("sdf_crore"))
        if inj is not None and park is not None:
            out["why"].append(f"on {fresh.get('date')} BB lent {inj:,.0f} cr of fresh money while banks parked {park:,.0f} cr at the SDF")
            if inj >= SEGMENTED_MIN_CRORE and park >= SEGMENTED_MIN_CRORE:
                out["segmented"] = True
                out["why"].append("a two-speed market: some banks borrow from BB while others park surplus at the floor - the "
                                  "average hides it (the two flows are shown, not netted)")
    return out


# ── 2. drivers ───────────────────────────────────────────────────────────────────────────────────────────────────
def fx_taka_effect(rows: List[dict]) -> dict:
    """Taka that BB's dollar auctions added to / drained from the banks: USD million x weighted-average rate / 10 = BDT crore.
    A BUY (BB buys dollars) pays taka INTO the market (+); a SELL takes taka OUT (-). A type that is neither is listed, never guessed."""
    crore, items, unknown = 0.0, [], []
    for r in rows or []:
        usd, rate = _num(r.get("accepted_amount_usd_mill")), _num(r.get("weighted_avg_rate"))
        kind = str(r.get("auction_type") or "").strip().lower()
        if usd is None or rate is None:
            continue
        if kind in ("buy", "bb buy"):
            sign = 1.0
        elif kind in ("sell", "bb sell"):
            sign = -1.0
        else:
            unknown.append(str(r.get("auction_type")))
            continue
        v = round(sign * usd * rate / 10.0, 2)
        crore += v
        items.append({"date": str(_d(r.get("auction_date"))), "type": kind, "usd_mn": usd, "rate": rate, "crore": v})
    return {"crore": round(crore, 2) if items else None, "items": items, "unknown_types": unknown}


def stock_movers(now: List[dict], then: List[dict], min_crore: float = MOVER_MIN_CRORE) -> List[dict]:
    """OMO facilities whose outstanding STOCK changed by at least `min_crore` between two days (every instrument the data holds)."""
    prev = {i["instrument"]: i for i in then or []}
    out = []
    for i in now or []:
        before = _num((prev.get(i["instrument"]) or {}).get("outstanding_crore")) or 0.0
        change = round((_num(i.get("outstanding_crore")) or 0.0) - before, 2)
        if abs(change) >= min_crore:
            out.append({"instrument": i["instrument"], "direction": i.get("direction"), "change_crore": change,
                        "now_crore": _num(i.get("outstanding_crore")), "then_crore": round(before, 2)})
    for gone in then or []:                       # a facility that disappeared entirely
        if gone["instrument"] not in {i["instrument"] for i in now or []} and abs(_num(gone.get("outstanding_crore")) or 0) >= min_crore:
            out.append({"instrument": gone["instrument"], "direction": gone.get("direction"),
                        "change_crore": -round(_num(gone["outstanding_crore"]), 2), "now_crore": 0.0,
                        "then_crore": _num(gone["outstanding_crore"])})
    return sorted(out, key=lambda m: -abs(m["change_crore"]))


def securities_net(days: List[dict]) -> dict:
    """Government securities cash over days: coupons + principal + buyback + sukuk coming IN, minus auction settlements going OUT.
    Positive = adds cash to the banks. None when ANY day's flows are not confirmed - a partial sum would look complete."""
    known = [d for d in days or [] if d.get("flows_confirmed")]
    if not days or len(known) != len(days):
        return {"crore": None, "days": len(days or []), "confirmed_days": len(known)}
    return {"crore": round(sum((d.get("govt_inflow_crore") or 0) - (d.get("auction_out_crore") or 0) for d in days), 2),
            "days": len(days), "confirmed_days": len(known)}


def week_drivers(now_omo: Optional[dict], then_omo: Optional[dict], past_days: List[dict], fx: dict,
                 call_now: Optional[dict], call_then: Optional[dict]) -> dict:
    """What moved liquidity over the last week. Components are listed apart and never added into one figure: they are different
    kinds of fact (a change in BB's stock, a settled cash flow, a dollar operation)."""
    nl_now = _num(((now_omo or {}).get("totals") or {}).get("net_injected_crore"))
    nl_then = _num(((then_omo or {}).get("totals") or {}).get("net_injected_crore"))
    cn, ct = _num((call_now or {}).get("rate")), _num((call_then or {}).get("rate"))
    return {
        "bb_net_lending_now_crore": nl_now,
        # a STOCK shown as a plain number, not an alert: short repo rolls over every week, so ~35,000 cr "maturing within 7 days" is the normal
        # state of the market and would be on permanently. What matters is how much is NOT rolled, which the next week's stock change shows.
        "bb_lending_maturing_7d_crore": _num(((now_omo or {}).get("totals") or {}).get("injected_maturing_7d_crore")),
        "bb_net_lending_change_crore": None if nl_now is None or nl_then is None else round(nl_now - nl_then, 2),
        "movers": stock_movers((now_omo or {}).get("instruments") or [], (then_omo or {}).get("instruments") or []),
        "securities": securities_net(past_days),
        "fx": fx,
        "call_change_bp": None if cn is None or ct is None else round((cn - ct) * 100, 1),
    }


# ── 3. outlook ───────────────────────────────────────────────────────────────────────────────────────────────────
def is_working_day(d: datetime.date, holidays) -> bool:
    return d.weekday() in WORKING_WEEKDAYS and d not in holidays


def flow_calendar(ladder_days: List[dict], holidays, auction_horizon: Optional[datetime.date], today: datetime.date,
                  n: int = HORIZON_WORKING_DAYS, events: Optional[List[dict]] = None) -> List[dict]:
    """The next `n` working days. Securities net = coupons/principal/buyback/sukuk in minus auction settlements out (+ adds cash).
    A day past the auction calendar BB has published has an UNKNOWN net (None): BB auctions weekly and a missing calendar is not
    'no borrowing'. OMO roll-off is shown as its own column (maturing facilities are usually refinanced) and is NOT in the net."""
    by_date = {d["date"]: d for d in ladder_days or []}
    out: List[dict] = []
    d = today
    while len(out) < n:
        d += datetime.timedelta(days=1)
        if d.weekday() not in WORKING_WEEKDAYS:
            continue
        if d in holidays:
            out.append({"date": str(d), "weekday": d.strftime("%a"), "holiday": True, "securities_net_crore": None,
                        "govt_inflow_crore": None, "auction_out_crore": None, "omo_rolloff_crore": None, "basis": "bank holiday",
                        "events": []})
            continue
        row = by_date.get(str(d))
        if row is None:
            out.append({"date": str(d), "weekday": d.strftime("%a"), "holiday": False, "securities_net_crore": None,
                        "govt_inflow_crore": None, "auction_out_crore": None, "omo_rolloff_crore": None,
                        "basis": "not in the ladder", "events": []})
            continue
        beyond = auction_horizon is None or d > auction_horizon
        confirmed = bool(row.get("flows_confirmed"))
        inflow, aout = row.get("govt_inflow_crore"), row.get("auction_out_crore")
        net = None if beyond else round((inflow or 0) - (aout or 0), 2)
        basis = ("auction calendar not published" if beyond else
                 "confirmed" if confirmed else "calendar (offered amounts; BB may accept less)")
        out.append({"date": str(d), "weekday": d.strftime("%a"), "holiday": False, "securities_net_crore": net,
                    "govt_inflow_crore": None if beyond else inflow, "auction_out_crore": None if beyond else aout,
                    "omo_rolloff_crore": row.get("omo_roll_net_crore"), "basis": basis,
                    "events": [e for e in events or [] if e.get("when") == str(d)]})
    return out


# ── 4. statements ────────────────────────────────────────────────────────────────────────────────────────────────
def _stmt(sid, severity, headline, detail, numbers, evidence) -> dict:
    return {"id": sid, "severity": severity, "headline": headline, "detail": detail, "numbers": numbers, "evidence": evidence}


def rank_insights(reg: dict, drivers: dict, calendar: List[dict], omo: Optional[dict], news: dict,
                  fx_last_date: Optional[str], omo_as_of: Optional[str], auction_horizon: Optional[str]) -> List[dict]:
    """Ranked statements (alert > watch > info, then by size). Each carries the numbers it rests on and where to check them."""
    out: List[dict] = []
    label = reg.get("label")
    sev = {TIGHT: "alert", SURPLUS: "info", BALANCED: "info", UNKNOWN: "watch"}[label]
    head = {TIGHT: "Liquidity is TIGHT", SURPLUS: "Liquidity is in SURPLUS", BALANCED: "Liquidity is BALANCED",
            UNKNOWN: "Liquidity cannot be judged today"}[label]
    out.append(_stmt("regime", sev, head, " ".join(reg.get("why") or []),
                     {"call_pct": (reg["inputs"].get("call") or {}).get("rate"),
                      "position_in_corridor_pct": reg.get("position_in_corridor_pct")},
                     [{"label": "Call money", "href": "/call-money"}, {"label": "Policy corridor", "href": "/forecast"}]))
    if reg.get("segmented"):
        f = reg["inputs"].get("fresh") or {}
        out.append(_stmt("segmented", "watch", "Two-speed market: banks borrow from BB while others park at the SDF",
                         "The average rate hides a split: weaker banks lean on BB's facilities while others hold surplus they cannot lend.",
                         {"fresh_injection_crore": f.get("injection_crore"), "sdf_parked_crore": f.get("sdf_crore")},
                         [{"label": "OMO operations", "href": "/omo"}]))
    bp = drivers.get("call_change_bp")
    if bp is not None and abs(bp) >= CALL_MOVE_BP:
        out.append(_stmt("call_move", "watch", f"Overnight call money moved {bp:+.0f}bp in a week",
                         "A move of this size inside the corridor is a change in how much cash banks are holding.",
                         {"call_change_bp": bp}, [{"label": "Call money", "href": "/call-money"}]))
    for m in (drivers.get("movers") or [])[:3]:
        out.append(_stmt(f"mover_{m['instrument']}", "info",
                         f"{m['instrument']} stock {'rose' if m['change_crore'] > 0 else 'fell'} {abs(m['change_crore']):,.0f} cr in a week",
                         f"{m['instrument']} outstanding {m['then_crore']:,.0f} -> {m['now_crore']:,.0f} cr "
                         f"({'a mop-up' if m.get('direction') == 'ABSORPTION' else 'BB lending'}).",
                         {"change_crore": m["change_crore"]}, [{"label": "OMO", "href": "/omo"}]))
    known = [d for d in calendar if d.get("securities_net_crore") is not None]
    drains = sorted((d for d in known if d["securities_net_crore"] <= -LARGE_DAY_CRORE), key=lambda d: d["securities_net_crore"])
    for d in drains[:2]:
        out.append(_stmt(f"drain_{d['date']}", "alert", f"{d['weekday']} {d['date']}: government securities drain {abs(d['securities_net_crore']):,.0f} cr",
                         f"Auction settlements {d['auction_out_crore'] or 0:,.0f} cr against {d['govt_inflow_crore'] or 0:,.0f} cr of coupons "
                         f"and principal ({d['basis']}).", {"securities_net_crore": d["securities_net_crore"]},
                         [{"label": "Day detail", "href": f"/drilldown?date={d['date']}"}, {"label": "Cash flows", "href": "/cashflows"}]))
    if known:
        cum = round(sum(d["securities_net_crore"] for d in known), 2)
        if cum <= -SQUEEZE_CUM_CRORE:
            out.append(_stmt("squeeze", "alert", f"Securities drain {abs(cum):,.0f} cr over the next {len(known)} working days",
                             "Cumulative government securities settlements net of coupons and principal over the known days.",
                             {"cumulative_crore": cum, "days": len(known)}, [{"label": "Cash flows", "href": "/cashflows"}]))
    if (drivers.get("fx") or {}).get("crore") is None:
        out.append(_stmt("fx_unknown", "info", "Dollar operations: no recent BB auction on record",
                         f"No BB FX auction in our data for the last {WEEK_DAYS} days (latest recorded: {fx_last_date or 'none'}). Press reports of dollar sales "
                         f"are listed below as news, not counted.", {}, [{"label": "FX intervention", "href": "/fx"}]))
    for e in (news.get("events") or [])[:4]:
        out.append(_stmt(f"news_{e['id']}", "info", f"News: {e.get('summary', '')[:110]}",
                         "Reported in the press, read by an AI reader and checked by code. NOT Bangladesh Bank data and NOT in any figure above.",
                         {"amount_crore": e.get("amount_crore")}, [{"label": "Liquidity Watch events", "href": "/liquidity-watch"}]))
    if omo_as_of or auction_horizon:
        out.append(_stmt("coverage", "info", "What this view knows",
                         f"BB's operations are known up to {omo_as_of or 'unknown'}; the auction calendar is published up to {auction_horizon or 'unknown'}. "
                         f"Days beyond are marked unknown, never zero.", {}, []))
    order = {"alert": 0, "watch": 1, "info": 2}
    return sorted(out, key=lambda s: order[s["severity"]])


# ── the brief: the facts it may describe, and which brief matches them ───────────────────────────────────────────────
def brief_facts(block: dict) -> dict:
    """The small, rounded, stable set of facts a liquidity brief describes. Rounding keeps the hash from changing on noise; every figure the
    brief can state is in here, and nothing from the news enters it except a COUNT (a press figure must not be restated as a BB fact)."""
    r = block.get("regime") or {}
    ins = r.get("inputs") or {}
    call, cor, fresh = ins.get("call") or {}, ins.get("corridor") or {}, ins.get("fresh") or {}
    d = block.get("drivers") or {}
    rnd = lambda v, n=0: None if v is None else round(float(v), n)
    cal = [c for c in block.get("calendar") or [] if c.get("securities_net_crore") is not None and not c.get("holiday")]
    drains = sorted((c for c in cal if c["securities_net_crore"] <= -LARGE_DAY_CRORE), key=lambda c: c["securities_net_crore"])[:2]
    k = block.get("knowledge") or {}
    return {
        "date": block.get("as_of"), "regime": r.get("label"), "segmented": bool(r.get("segmented")),
        "call_pct": rnd(call.get("rate"), 2), "call_provisional": bool(call.get("provisional")), "call_date": call.get("date"),
        "repo_pct": rnd(cor.get("repo"), 2), "slf_pct": rnd(cor.get("slf"), 2), "sdf_pct": rnd(cor.get("sdf"), 2),
        "fresh_date": fresh.get("date"), "fresh_injection_crore": rnd(fresh.get("injection_crore")), "fresh_sdf_crore": rnd(fresh.get("sdf_crore")),
        "net_lending_crore": rnd(d.get("bb_net_lending_now_crore")), "net_lending_change_crore": rnd(d.get("bb_net_lending_change_crore")),
        "call_change_bp": rnd(d.get("call_change_bp")),
        "movers": [{"instrument": m["instrument"], "change_crore": rnd(m["change_crore"])} for m in (d.get("movers") or [])[:3]],
        "securities_week_crore": rnd((d.get("securities") or {}).get("crore")),
        "fx_week_crore": rnd((d.get("fx") or {}).get("crore")),
        "drain_days": [{"date": c["date"], "weekday": c["weekday"], "net_crore": rnd(c["securities_net_crore"])} for c in drains],
        "omo_known_to": k.get("omo_known_to"), "auction_calendar_to": k.get("auction_calendar_to"),
        "news_events": len((block.get("news") or {}).get("events") or []),
    }


def facts_hash(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()


def latest_brief(session, h: str) -> Optional[dict]:
    """The brief for EXACTLY these facts: an AI-drafted one if it passed the checks, else the code-composed one. None if the table is
    missing or no brief has been written for them yet (the page then shows the ranked statements alone)."""
    try:
        row = session.execute(text(
            "SELECT text, model, generated_utc FROM morning_briefs WHERE facts_hash = :h AND status = 'accepted' "
            "ORDER BY CASE WHEN model = 'code' THEN 1 ELSE 0 END, generated_utc DESC LIMIT 1"), {"h": h}).fetchone()
    except Exception:
        session.rollback()
        return None
    if not row:
        return None
    return {"text": row[0], "model": row[1], "generated_utc": str(row[2]),
            "by": "code" if row[1] == "code" else "an AI model", "hash": h[:10]}


# ── glue: the only place that touches the database ───────────────────────────────────────────────────────────────
def dhaka_today(now: Optional[datetime.datetime] = None) -> datetime.date:
    return ((now or datetime.datetime.utcnow()) + datetime.timedelta(hours=6)).date()


def _call_series(session, limit: int = 12) -> List[dict]:
    rows = session.execute(text(
        "SELECT trade_date, SUM(amount_crore), SUM(average_rate_pct * amount_crore) FROM call_money_rates "
        "WHERE product = 'Overnight' AND average_rate_pct IS NOT NULL GROUP BY trade_date ORDER BY trade_date DESC LIMIT :n"),
        {"n": limit}).fetchall()
    return [{"date": str(_d(r[0])), "rate": round(float(r[2]) / float(r[1]), 4), "amount_crore": float(r[1])}
            for r in rows if r[1]]


def insights_block(session, today: Optional[datetime.date] = None) -> dict:
    today = today or dhaka_today()
    # call money: the latest overnight day, and the day a week earlier
    series = _call_series(session)
    call_now = None
    call_then = None
    if series:
        latest = series[0]
        call_now = {"rate": latest["rate"], "date": latest["date"], "provisional": _d(latest["date"]) >= today}
        week = _d(latest["date"]) - datetime.timedelta(days=WEEK_DAYS)
        older = [s for s in series if _d(s["date"]) <= week]
        call_then = {"rate": older[0]["rate"], "date": older[0]["date"]} if older else None
    # the policy corridor in force
    row = session.execute(text("SELECT first_seen_date, repo, slf, sdf FROM policy_rate_snapshots "
                               "ORDER BY first_seen_date DESC, id DESC LIMIT 1")).fetchone()
    corridor = ({"effective_date": str(_d(row[0])), "repo": row[1], "slf": row[2], "sdf": row[3]} if row else None)
    # BB's operations: stock now, a week earlier, and what was dealt on the last published day
    omo_now = OL.injection_summary(session)
    as_of = _d(omo_now.get("as_of"))
    omo_then = OL.injection_summary(session, as_of=as_of - datetime.timedelta(days=WEEK_DAYS)) if as_of else None
    fresh = None
    if as_of:
        f = session.execute(text(
            "SELECT direction, SUM(accepted_bdt_crore) FROM omo_transactions WHERE transaction_date = :d AND accepted_bdt_crore > 0 "
            "GROUP BY direction"), {"d": str(as_of)}).fetchall()
        got = {r[0]: float(r[1] or 0) for r in f}
        fresh = {"date": str(as_of), "injection_crore": got.get("INJECTION", 0.0), "sdf_crore": got.get("ABSORPTION", 0.0)}
    reg = regime(call_now, corridor, fresh)
    # the ladder: the past week (what settled) and the next weeks (what is scheduled)
    start = (as_of or today) - datetime.timedelta(days=6)
    ladder = LL.liquidity_ladder(session, date_from=str(start), date_to=str(today + datetime.timedelta(days=21)), today=today)
    days = ladder["days"]
    holidays = {_d(r[0]) for r in session.execute(text("SELECT calendar_date FROM holiday_calendar")).fetchall()}
    # only WORKING days carry a flow row; a Friday or a holiday with no row is not an unconfirmed day
    past = [d for d in days if as_of and start <= _d(d["date"]) <= as_of and is_working_day(_d(d["date"]), holidays)]
    horizon = _d(ladder.get("auction_horizon"))
    # dollars
    fx_rows = [dict(zip(("auction_date", "auction_type", "accepted_amount_usd_mill", "weighted_avg_rate"), r)) for r in session.execute(text(
        "SELECT auction_date, auction_type, accepted_amount_usd_mill, weighted_avg_rate FROM fx_auction_results "
        "WHERE auction_date >= :a AND auction_date <= :b"), {"a": str(today - datetime.timedelta(days=WEEK_DAYS)), "b": str(today)}).fetchall()]
    fx_last = session.execute(text("SELECT MAX(auction_date) FROM fx_auction_results")).scalar()
    fx = fx_taka_effect(fx_rows)
    drivers = week_drivers(omo_now, omo_then, past, fx, call_now, call_then)
    # news: listed, never summed
    nb = NL.news_block(session, today=today, days=NEWS_LOOKBACK_DAYS + 16)
    recent = [e for e in nb.get("events", []) if e.get("when") and e["when"] >= str(today - datetime.timedelta(days=NEWS_LOOKBACK_DAYS))]
    news = {"events": recent, "unconfirmed": len(nb.get("unconfirmed") or []), "note": nb.get("note")}
    cal = flow_calendar(days, holidays, horizon, today, HORIZON_WORKING_DAYS, events=nb.get("events"))
    block = {
        "as_of": str(today), "unit": "BDT crore",
        "sign_convention": "flows: + adds cash to the banks, - drains it. BB net lending is a stock, not a flow.",
        "regime": reg, "drivers": drivers, "calendar": cal, "news": news,
        "knowledge": {"omo_known_to": str(as_of) if as_of else None, "auction_calendar_to": str(horizon) if horizon else None,
                      "last_fx_auction": str(_d(fx_last)) if fx_last else None},
        "insights": rank_insights(reg, drivers, cal, omo_now, news, str(_d(fx_last)) if fx_last else None,
                                  str(as_of) if as_of else None, str(horizon) if horizon else None),
    }
    block["brief"] = latest_brief(session, facts_hash(brief_facts(block)))
    return block
