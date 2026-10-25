"""How much liquidity is Bangladesh Bank putting into the market, and through what.

The OMO page showed outstanding balances as a chart and a table of operations,
but never the one number a desk asks first: how much is BB injecting right now,
facility by facility, against how much it is mopping up. This is that answer.

Two things to be careful about, both learned the hard way on this dataset:

  * **Injection is a STOCK.** What BB has put in is the sum of tranches that have
    been dealt and not yet matured (transaction_date <= as_of < maturity_date).
    Adding up what was dealt in a window would double-count every rolled tranche.
    The window figures here are labelled "dealt" and sit beside the stock, never
    in place of it.
  * **as_of is the last day BB published, not today.** OMO press releases arrive a
    day or two late; measuring the stock at `today` would count days whose deals we
    cannot yet know, silently understating the injection.

Every instrument present in the data is returned - none is looked up in a fixed
list - so a new BB facility appears the day it is dealt rather than never.

api/ deploys rooted at api/: stdlib and sqlalchemy only.
"""
import datetime
from typing import Dict, Optional

from sqlalchemy import text

# BB's Shariah-compliant liquidity facilities. Shown as their own subtotal
# because the Islamic banks cannot use the conventional repo window.
ISLAMIC_FACILITIES = ("IBLF", "MLS")


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


def injection_summary(session, as_of=None, window_days: int = 30) -> Dict:
    last = session.execute(text(
        "SELECT MAX(transaction_date) FROM omo_transactions WHERE accepted_bdt_crore > 0"
    )).scalar()
    day = _d(as_of) or _d(last)
    if day is None:
        return {"as_of": None, "window_days": window_days, "instruments": [], "totals": None,
                "note": "no OMO data"}
    since = day - datetime.timedelta(days=window_days)

    stock = session.execute(text(
        "SELECT instrument, direction, accepted_bdt_crore, rate_pct, maturity_date "
        "FROM omo_transactions WHERE accepted_bdt_crore > 0 "
        "AND transaction_date <= :a AND maturity_date > :a"), {"a": str(day)}).fetchall()
    dealt = session.execute(text(
        "SELECT instrument, direction, accepted_bdt_crore FROM omo_transactions "
        "WHERE accepted_bdt_crore > 0 AND transaction_date > :s AND transaction_date <= :a"),
        {"s": str(since), "a": str(day)}).fetchall()

    d7, d30 = day + datetime.timedelta(days=7), day + datetime.timedelta(days=30)
    by: Dict[str, Dict] = {}

    def slot(instr, direction):
        return by.setdefault(instr, {
            "instrument": instr, "direction": direction, "islamic": instr in ISLAMIC_FACILITIES,
            "outstanding_crore": 0.0, "tranches": 0, "_rate_w": 0.0, "_rate_n": 0.0,
            "next_maturity": None, "maturing_7d_crore": 0.0, "maturing_30d_crore": 0.0,
            "dealt_window_crore": 0.0, "deals_window": 0})

    for instr, direction, acc, rate, mat in stock:
        s = slot(instr, direction)
        a = float(acc or 0)
        m = _d(mat)
        s["outstanding_crore"] += a
        s["tranches"] += 1
        if rate is not None:
            s["_rate_w"] += float(rate) * a
            s["_rate_n"] += a
        if m is not None:
            if s["next_maturity"] is None or m < s["next_maturity"]:
                s["next_maturity"] = m
            if m <= d7:
                s["maturing_7d_crore"] += a
            if m <= d30:
                s["maturing_30d_crore"] += a
    for instr, direction, acc in dealt:
        s = slot(instr, direction)
        s["dealt_window_crore"] += float(acc or 0)
        s["deals_window"] += 1

    inj_total = sum(v["outstanding_crore"] for v in by.values() if v["direction"] == "INJECTION")
    out = []
    for v in by.values():
        n = v.pop("_rate_n")
        w = v.pop("_rate_w")
        v["wtd_rate_pct"] = round(w / n, 4) if n else None
        v["next_maturity"] = str(v["next_maturity"]) if v["next_maturity"] else None
        v["share_of_injected_pct"] = (round(100.0 * v["outstanding_crore"] / inj_total, 1)
                                      if v["direction"] == "INJECTION" and inj_total else None)
        for k in ("outstanding_crore", "maturing_7d_crore", "maturing_30d_crore",
                  "dealt_window_crore"):
            v[k] = round(v[k], 2)
        out.append(v)
    out.sort(key=lambda v: (v["direction"] != "INJECTION", -v["outstanding_crore"]))

    def tot(direction, key, islamic=None):
        return round(sum(v[key] for v in out if v["direction"] == direction
                         and (islamic is None or v["islamic"] == islamic)), 2)

    injected = tot("INJECTION", "outstanding_crore")
    absorbed = tot("ABSORPTION", "outstanding_crore")
    return {
        "as_of": str(day), "window_days": window_days, "since": str(since),
        "unit": "BDT crore", "instruments": out,
        "totals": {
            "injected_crore": injected,
            "absorbed_crore": absorbed,
            "net_injected_crore": round(injected - absorbed, 2),
            "islamic_injected_crore": tot("INJECTION", "outstanding_crore", True),
            "conventional_injected_crore": tot("INJECTION", "outstanding_crore", False),
            "dealt_injected_window_crore": tot("INJECTION", "dealt_window_crore"),
            "dealt_absorbed_window_crore": tot("ABSORPTION", "dealt_window_crore"),
            "injected_maturing_7d_crore": tot("INJECTION", "maturing_7d_crore"),
            "injected_maturing_30d_crore": tot("INJECTION", "maturing_30d_crore"),
        },
        "note": ("Stock measured on the last day BB's operations were published "
                 f"({day}); later days are not yet known."),
    }
