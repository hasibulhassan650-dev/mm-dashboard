"""
validate.py — data-integrity monitor.

Plausibility rules that catch the *classes* of bug we've hit (0% yields, OMO
instrument mislabels, write failures) the moment they recur — without
false-flagging genuine data (IBLF's real ~4% rate, the 2020-21 COVID yield lows).

`integrity_check()` scans the stored data and returns a structured report:
  { "ok": bool, "issue_count": int, "issues": [ "<table> <date> <detail>", ... ],
    "by_table": { table: count } }

Run after every refresh/reconcile; surfaced at /api/meta/quality so the
dashboard shows data health and you never have to spot a mislabel by eye again.
"""
import datetime
from sqlalchemy import text
from db import get_session

# genuine BD ultra-low yield window (COVID liquidity glut) — sub-1% is REAL here
_COVID_LO = datetime.date(2020, 9, 1)
_COVID_HI = datetime.date(2021, 10, 31)

_OMO_INSTRUMENTS = {"CB_REPO", "SLF", "IBLF", "AR", "SDF"}
_YIELD_TENORS = {"14D", "28D", "91D", "182D", "364D", "2Y", "3Y", "3Y_FRTB",
                 "5Y", "10Y", "15Y", "20Y", "25Y"}


def integrity_check(limit_per_rule: int = 8) -> dict:
    s = get_session()
    issues: list[str] = []
    by_table: dict[str, int] = {}

    def add(table: str, msg: str):
        by_table[table] = by_table.get(table, 0) + 1
        if sum(1 for i in issues if i.startswith(table)) < limit_per_rule:
            issues.append(f"{table}: {msg}")

    def q(sql, **p):
        return s.execute(text(sql), p).fetchall()

    try:
        # ---- treasury yields ----
        for r in q("SELECT auction_date, tenor_label, cutoff_yield_pct FROM primary_yield_snapshots "
                   "WHERE cutoff_yield_pct <= 0 OR cutoff_yield_pct > 30"):
            add("yields", f"{r[0]} {r[1]} implausible cut-off {r[2]}%")
        for r in q("SELECT auction_date, tenor_label, cutoff_yield_pct FROM primary_yield_snapshots "
                   "WHERE cutoff_yield_pct > 0 AND cutoff_yield_pct < 1.0 "
                   "AND (auction_date < :lo OR auction_date > :hi)", lo=_COVID_LO, hi=_COVID_HI):
            add("yields", f"{r[0]} {r[1]} sub-1% ({r[2]}%) outside COVID window — likely parse error")
        for r in q("SELECT DISTINCT tenor_label FROM primary_yield_snapshots"):
            if r[0] not in _YIELD_TENORS:
                add("yields", f"unknown tenor label '{r[0]}'")

        # ---- OMO (the instrument-mislabel guard) ----
        for r in q("SELECT transaction_date, instrument, tenor_days, rate_pct FROM omo_transactions "
                   "WHERE instrument = 'CB_REPO' AND rate_pct IS NOT NULL AND (rate_pct < 8 OR rate_pct > 12)"):
            add("omo", f"{r[0]} CB_REPO rate {r[3]}% — outside policy band (likely IBLF/AR mislabel)")
        for r in q("SELECT transaction_date, tenor_days FROM omo_transactions "
                   "WHERE instrument = 'SDF' AND tenor_days <> 1"):
            add("omo", f"{r[0]} SDF tenor {r[1]}D — SDF is overnight-only")
        for r in q("SELECT DISTINCT instrument FROM omo_transactions"):
            if r[0] not in _OMO_INSTRUMENTS:
                add("omo", f"unknown instrument '{r[0]}'")
        for r in q("SELECT transaction_date, instrument, rate_pct FROM omo_transactions "
                   "WHERE rate_pct IS NOT NULL AND (rate_pct < 0 OR rate_pct > 30)"):
            add("omo", f"{r[0]} {r[1]} rate {r[2]}% out of range")
        for r in q("SELECT transaction_date, instrument FROM omo_transactions WHERE accepted_bdt_crore < 0"):
            add("omo", f"{r[0]} {r[1]} negative accepted amount")

        # ---- coupon amounts (the face×rate/freq guard) ----
        # Every standard periodic coupon must equal outstanding × rate/100 ÷
        # payments-per-year (2 half-yearly, 4 quarterly). Genuine short FIRST
        # coupons (ACT365_SHORT_FIRST) are legitimately pro-rated and excluded.
        # Catches any regression back to day-count amounts (the 4608 vs 4621 bug).
        for r in q("SELECT isin, scheduled_date, amount_bdt_mill, coupon_rate_used_pct, "
                   "outstanding_used_bdt_mill, calc_method FROM coupon_events "
                   "WHERE calc_method LIKE 'APPROX_%' AND outstanding_used_bdt_mill > 0 "
                   "AND coupon_rate_used_pct > 0"):
            div = 2 if str(r[5]).endswith("HFLY") else 4
            expected = r[4] * (r[3] / 100) / div
            if abs((r[2] or 0) - expected) > max(0.5, expected * 0.001):
                add("coupons", f"{r[0]} {r[1]} amount {r[2]:.2f} != face×rate/{div} ({expected:.2f})")

        # ---- call money / ref rates ----
        for r in q("SELECT trade_date, average_rate_pct FROM call_money_rates "
                   "WHERE average_rate_pct IS NOT NULL AND (average_rate_pct <= 0 OR average_rate_pct > 50)"):
            add("callmoney", f"{r[0]} avg rate {r[1]}% out of range")
        for r in q("SELECT trade_date, highest_rate_pct, lowest_rate_pct FROM call_money_rates "
                   "WHERE highest_rate_pct IS NOT NULL AND lowest_rate_pct IS NOT NULL AND highest_rate_pct < lowest_rate_pct"):
            add("callmoney", f"{r[0]} high {r[1]} < low {r[2]}")
        for r in q("SELECT trade_date, rate_type, product, rate_pct FROM ref_rates "
                   "WHERE rate_pct IS NOT NULL AND (rate_pct <= 0 OR rate_pct > 50)"):
            add("refrate", f"{r[0]} {r[1]} {r[2]} rate {r[3]}% out of range")

        # ---- FX ----
        for r in q("SELECT auction_date, weighted_avg_rate FROM fx_auction_results "
                   "WHERE weighted_avg_rate IS NOT NULL AND (weighted_avg_rate < 80 OR weighted_avg_rate > 200)"):
            add("fx", f"{r[0]} USD/BDT {r[1]} implausible")

        # ---- freshness expectations (catch silent-stale failures) ----
        # A fetch that "succeeds" but stores nothing new must FAIL here, not
        # pass unnoticed (July 2026: FY-rollover left the auction calendar
        # seed stale → "no auctions next month" presented as fact).
        today = datetime.date.today()

        def _max_date(sql) -> datetime.date | None:
            v = s.execute(text(sql)).scalar()
            if isinstance(v, str):
                v = datetime.date.fromisoformat(v[:10])
            return v

        FRESHNESS_RULES = [
            # (dataset, sql for max date, min acceptable, description)
            ("auctions_forward", "SELECT MAX(settlement_date) FROM auction_events",
             today + datetime.timedelta(days=7),
             "no planned auction ≥7 days ahead — BB auctions T-bills weekly; calendar likely stale"),
            ("secondary", "SELECT MAX(settlement_date) FROM mtm_snapshots",
             today - datetime.timedelta(days=4),
             "secondary MTM stale >4 days (weekend+holiday buffer)"),
            ("callmoney", "SELECT MAX(trade_date) FROM call_money_rates",
             today - datetime.timedelta(days=4),
             "call money stale >4 days"),
            ("refrate", "SELECT MAX(trade_date) FROM ref_rates",
             today - datetime.timedelta(days=4),
             "reference rates stale >4 days"),
            ("omo", "SELECT MAX(transaction_date) FROM omo_transactions",
             today - datetime.timedelta(days=10),
             "no OMO transaction in 10 days — publication gap this long is implausible"),
            ("yields", "SELECT MAX(auction_date) FROM primary_yield_snapshots",
             today - datetime.timedelta(days=14),
             "no primary auction result in 14 days"),
            ("flows_forward", "SELECT MAX(flow_date) FROM daily_net_flow",
             today + datetime.timedelta(days=30),
             "forward flow window <30 days — coupon/maturity projection broke"),
        ]
        for name, sql, min_ok, desc in FRESHNESS_RULES:
            try:
                mx = _max_date(sql)
            except Exception as exc:
                add("freshness", f"{name}: check failed ({exc})")
                continue
            if mx is None or mx < min_ok:
                add("freshness", f"{name}: latest={mx} (need ≥{min_ok}) — {desc}")

        # ---- reserves / remittance ----
        for r in q("SELECT month, gross_reserves_usd_mn, net_reserves_bpm6_usd_mn FROM reserves_monthly "
                   "WHERE gross_reserves_usd_mn IS NOT NULL AND (gross_reserves_usd_mn < 0 OR gross_reserves_usd_mn > 60000 "
                   "OR (net_reserves_bpm6_usd_mn IS NOT NULL AND net_reserves_bpm6_usd_mn > gross_reserves_usd_mn))"):
            add("reserves", f"{r[0]} gross {r[1]} / net {r[2]} implausible")
        for r in q("SELECT month, remittance_usd_mn FROM remittance_monthly "
                   "WHERE remittance_usd_mn IS NOT NULL AND (remittance_usd_mn < 0 OR remittance_usd_mn > 10000)"):
            add("remittance", f"{r[0]} remittance {r[1]} mn implausible")
    finally:
        s.close()

    return {
        "ok": len(by_table) == 0,
        "issue_count": sum(by_table.values()),
        "issues": issues,
        "by_table": by_table,
    }


if __name__ == "__main__":
    import json
    rep = integrity_check()
    print(json.dumps(rep, indent=2, default=str))
