"""
deep_audit.py — the weekly full-history audit.

The integrity gate deliberately looks at recent windows: the OMO ledger checks
30 days, the ladder 60. That keeps every refresh fast, but it means an error
that lands outside the window is never re-examined, and the two full-history
audits that found the real damage in Sep-2026 were both run BY HAND.

This runs them on a schedule instead:
  1. OMO ledger, ALL history — our live tranches maturing on day D must equal
     what BB printed as maturing on D. A shortfall is a missing or misdated
     injection, an excess is a phantom tranche; either silently corrupts
     outstanding (the 22-Jun IBLF case: 5,703 -> 13,622 on a real net of 3,114).
  2. daily_net_flow, ALL dates — the materialised ladder must equal a fresh
     recompute from the coupon / maturity / auction rows that back it.

Prints a markdown report (deep_audit.md) for the daily digest to carry, and
exits non-zero if anything is off, so the workflow raises an alert.
"""
import collections
import datetime
import sys

from sqlalchemy import text

from db import get_session

# BB pays principal plus profit/interest, so grow each tranche before comparing.
LEDGER_TOL_PCT, LEDGER_TOL_ABS = 0.015, 10.0
# Known BB-side quirks: real, understood, and not ours to fix. They are still
# REPORTED, as a separate section — never silently dropped, or this list becomes
# the place mistakes go to hide.
KNOWN_EXCEPTIONS = {
    ("2026-05-17", "AR"): "BB matured the 19-Apr AR 28D on 18-May, a day after T+28 "
                          "(verified against the press releases in the Sep-2026 audit)",
}
# Tranches placed before our OMO history starts can mature inside it with no
# record of the injection — that is the history boundary, not a missing release.
PRE_WINDOW_DAYS = 180


def _d(v):
    return datetime.date.fromisoformat(v[:10]) if isinstance(v, str) else v


def omo_ledger_full(s) -> tuple:
    printed = {}
    for inst, day, tot in s.execute(text(
            "SELECT instrument, transaction_date, SUM(COALESCE(maturity_bdt_crore,0)) "
            "FROM omo_transactions GROUP BY instrument, transaction_date")):
        if tot and float(tot) > 0:
            printed[(inst, _d(day))] = float(tot)

    live = collections.defaultdict(float)
    for inst, mat, acc, rate, ten in s.execute(text(
            "SELECT instrument, maturity_date, accepted_bdt_crore, rate_pct, tenor_days "
            "FROM omo_transactions WHERE accepted_bdt_crore > 0")):
        live[(inst, _d(mat))] += float(acc) * (1 + float(rate or 0) / 100 * int(ten or 0) / 365)

    hist_start = _d(s.execute(text("SELECT MIN(transaction_date) FROM omo_transactions")).scalar())
    out, known = [], []
    for (inst, day), bb in sorted(printed.items(), key=lambda x: (x[0][1], x[0][0])):
        # MLS is repaid in parts as remittance claims settle, so a per-day match
        # is meaningless for it (verified against BB's own prints, Aug-Sep 2026).
        if inst == "MLS":
            continue
        ours = live.get((inst, day), 0.0)
        if ours < bb and hist_start and day - datetime.timedelta(days=PRE_WINDOW_DAYS) < hist_start:
            continue
        if abs(ours - bb) > max(LEDGER_TOL_PCT * bb, LEDGER_TOL_ABS):
            kind = "missing/misdated injection" if ours < bb else "phantom tranche"
            msg = f"{day} {inst}: our tranches {ours:,.0f} cr vs BB printed {bb:,.0f} cr — {kind}"
            why = KNOWN_EXCEPTIONS.get((str(day), inst))
            (known if why else out).append(msg + (f" · KNOWN: {why}" if why else ""))
    return out, known


def flows_full(s) -> list:
    ladder = {}
    for d, c, p, a in s.execute(text(
            "SELECT flow_date, coupon_inflow_bdt_mill, principal_inflow_bdt_mill, "
            "auction_outflow_planned_mill FROM daily_net_flow")):
        ladder[_d(d)] = (float(c or 0), float(p or 0), float(a or 0))

    ev = collections.defaultdict(lambda: [0.0, 0.0, 0.0])
    for d, v in s.execute(text("SELECT payment_date, amount_bdt_mill FROM coupon_events")):
        ev[_d(d)][0] += float(v or 0)
    for d, v in s.execute(text("SELECT payment_date, principal_bdt_mill FROM maturity_events")):
        ev[_d(d)][1] += float(v or 0)
    for d, v in s.execute(text("SELECT settlement_date, offered_amount_bdt_mill FROM auction_events")):
        if d is not None:
            ev[_d(d)][2] += float(v or 0)

    if not ladder:
        return ["daily_net_flow is empty — the ladder was never built"]
    lo, hi = min(ladder), max(ladder)
    out = []
    for day in sorted(set(ladder) | {d for d in ev if lo <= d <= hi}):
        have = ladder.get(day, (0.0, 0.0, 0.0))
        want = ev.get(day, [0.0, 0.0, 0.0])
        for i, label in enumerate(("coupon", "principal", "auction")):
            if abs(have[i] - want[i]) > max(5.0, want[i] * 0.005):
                out.append(f"{day} {label}: ladder {have[i]:,.0f} vs events {want[i]:,.0f} mill")
    return out


def main() -> int:
    s = get_session()
    try:
        (ledger, known), flows = omo_ledger_full(s), flows_full(s)
    finally:
        s.close()

    lines = ["## Weekly deep audit (full history)",
             f"_{datetime.datetime.utcnow():%Y-%m-%d %H:%M UTC}_", ""]
    for title, found, note in (
            ("OMO ledger vs BB's printed maturities", ledger,
             "every live tranche maturing on a day must equal what BB printed as maturing"),
            ("Liquidity ladder vs its source events", flows,
             "daily_net_flow must equal a fresh recompute from coupon/maturity/auction rows")):
        if found:
            lines.append(f"### ❌ {title} — {len(found)} mismatch(es)")
            lines.append(f"_{note}_\n")
            lines += [f"- {x}" for x in found[:25]]
            if len(found) > 25:
                lines.append(f"- …and {len(found) - 25} more")
        else:
            lines.append(f"### ✅ {title} — clean")
            lines.append(f"_{note}_")
        lines.append("")

    if known:
        lines.append(f"### {len(known)} known exception(s) — understood, not ours to fix")
        lines += [f"- {x}" for x in known]
        lines.append("")
    body = "\n".join(lines)
    with open("deep_audit.md", "w", encoding="utf-8") as f:
        f.write(body)
    try:
        print(body)
    except UnicodeEncodeError:
        print(body.encode("ascii", "replace").decode("ascii"))
    return 1 if (ledger or flows) else 0


if __name__ == "__main__":
    sys.exit(main())
