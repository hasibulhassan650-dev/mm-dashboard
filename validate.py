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

_OMO_INSTRUMENTS = {"CB_REPO", "CM_REPO", "SLF", "IBLF", "MLS", "SLS", "SRF", "AR", "SDF"}
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

        # ---- OMO rate fingerprint: a label must match the rate it printed ----
        # Every BB facility carries a signature rate: SDF = corridor floor (~7.5,
        # overnight), SLF = ceiling (~11–11.5, overnight), repo/AR = policy rate
        # (~9.5–10), IBLF = an Islamic profit RANGE or a fixed rate ≤5, CM_REPO =
        # 4.75/90D, MLS = 5.25/28D. A label that contradicts its rate is a block-
        # attribution error in the parser. Mar–Jun 2026: 55 such rows, including
        # 13 SDF ABSORPTIONS stored as AR INJECTIONS (sign errors in net liquidity)
        # and the 15-Jun IBLF stored as CB_REPO that produced the 5,703→13,622 jump.
        # Bands are tolerant to modest policy moves; a regime change needs review.
        for r in q("SELECT transaction_date, instrument, tenor_days, rate_pct, rate_range "
                   "FROM omo_transactions WHERE accepted_bdt_crore > 0 AND rate_pct IS NOT NULL"):
            d, inst, ten, rate, rng = r[0], r[1], int(r[2] or 0), float(r[3]), r[4]
            lo = None
            if rng:
                try: lo = float(str(rng).split('-')[0])
                except Exception: lo = None
            want = None
            if ten == 1 and 7.0 <= rate <= 8.0:                          want = "SDF"
            elif ten == 1 and 10.5 <= rate <= 12.0:                      want = "SLF"
            elif rng and lo is not None and lo <= 7.0:                   want = "IBLF"
            elif ten == 90 and abs(rate - 4.75) < 1e-6:                  want = "CM_REPO"
            elif ten == 28 and abs(rate - 5.25) < 1e-6:                  want = "MLS"
            elif rate <= 5.0 and inst in ("AR", "CB_REPO", "SLF"):        want = "IBLF"
            elif 9.0 <= rate <= 10.5 and not rng and inst in ("IBLF", "MLS", "SLF", "SDF"):
                want = "CB_REPO/AR"
            ok = want is None or want == inst or (want == "CB_REPO/AR" and inst in ("CB_REPO", "AR"))
            if not ok:
                add("omo", f"{d} {inst} {ten}D @{rate}%{' ('+str(rng)+')' if rng else ''} — rate fingerprint "
                           f"says {want}: instrument mislabeled (parser block-attribution error)")
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

        today = datetime.date.today()

        # ---- OMO operational cadence (catch a missing / mislabeled BB release) ----
        # BB runs an OMO every working day, and CB Repo is the Tuesday operation.
        # A working day with NO operation — or a working Tuesday with NO CB Repo —
        # is almost always a BB press-release mistake: a duplicate 'as on' date
        # that superseded a real operation (Aug-2026: two "as on 06 August" files,
        # the earlier one really the 04-Aug Tuesday CB Repo, got dropped), or a
        # mislabel. We surface it and — because the holiday calendar can lag the
        # FY rollover and may not know the day — ASK whether it was a holiday
        # rather than asserting. Skip the last 2 days: results publish with a lag,
        # so "missing" there is not-yet-published, not a real gap.
        from calendar_utils import is_working_day
        # Normalise DB dates to date objects — Postgres returns date, SQLite str.
        def _dset(sql, **p):
            out = set()
            for r in q(sql, **p):
                v = r[0]
                out.add(datetime.date.fromisoformat(v[:10]) if isinstance(v, str) else v)
            return out
        hol = _dset("SELECT calendar_date FROM holiday_calendar")
        _lo = today - datetime.timedelta(days=21)
        _hi = today - datetime.timedelta(days=2)
        omo_dates = _dset(
            "SELECT DISTINCT transaction_date FROM omo_transactions "
            "WHERE transaction_date >= :lo AND transaction_date <= :hi", lo=_lo, hi=today)
        cbrepo_dates = _dset(
            "SELECT DISTINCT transaction_date FROM omo_transactions "
            "WHERE instrument = 'CB_REPO' AND accepted_bdt_crore > 0 AND transaction_date >= :lo AND transaction_date <= :hi",
            lo=_lo, hi=today)
        # Market-activity signature: a day the WHOLE money market traded. If call
        # money AND reference rates were also silent on a day OMO is missing, the
        # market was shut — a bank holiday the calendar may not list yet — so it
        # is NOT a missing/mislabeled release and must not fail the gate. This
        # auto-suppresses holidays (05-Aug, 26-Aug…) without seeding each one.
        cm_dates = _dset(
            "SELECT DISTINCT trade_date FROM call_money_rates "
            "WHERE trade_date >= :lo AND trade_date <= :hi", lo=_lo, hi=today)
        rr_dates = _dset(
            "SELECT DISTINCT trade_date FROM ref_rates "
            "WHERE trade_date >= :lo AND trade_date <= :hi", lo=_lo, hi=today)
        # Only flag a gap we can PROVE is real: one we hold later data past. A
        # missing day at the tail is just not-yet-published (results lag, more so
        # over the Fri–Sat weekend), not a genuine hole.
        max_omo = max(omo_dates) if omo_dates else None
        d = _lo
        while d <= _hi:
            if max_omo is not None and d < max_omo and is_working_day(d, hol):  # Mon–Thu + Sun, minus known holidays
                market_shut = d not in cm_dates and d not in rr_dates
                if d not in omo_dates:
                    if not market_shut:   # market traded but no OMO → real gap
                        add("omo", f"no OMO operation on working day {d} ({d:%a}) — BB operates "
                                   f"every working day and the money market WAS open (call money / "
                                   f"ref rates traded); a BB press release is likely missing or "
                                   f"duplicate-dated. Confirm {d} was not a bank holiday.")
                    # else: whole market shut → bank holiday, auto-suppressed
                elif d.weekday() == 1 and d not in cbrepo_dates:   # Tuesday WITH ops but no CB Repo
                    add("omo", f"no CB_REPO on working Tuesday {d} — CB Repo is the Tuesday "
                               f"operation; likely a mislabeled/duplicate BB release swallowed "
                               f"it. Confirm {d} was a working day (not a bank holiday).")
            d += datetime.timedelta(days=1)

        # ---- OMO ledger reconciliation: live tranches vs BB's printed maturities ----
        # BB prints, per instrument per day, the amount that MATURED. Our outstanding
        # is the sum of live tranches, so the tranches WE hold maturing on day D must
        # equal BB's printed maturity for D. BB's figure is principal PLUS interest/
        # profit, so each tranche is grown by rate×tenor/365 before comparing, which
        # lets the tolerance be tight. A shortfall = a missing/misdated injection (a
        # dropped or double-dated release); an excess = a phantom tranche. Either one
        # silently corrupts outstanding (Jun-2026: IBLF read 5,703→13,622 on a real
        # net of 3,114 because the 15-Jun injection was never stored). Recent window
        # only — tranches placed before our history began can't be reconciled, and
        # results publish with a lag.
        _rlo = today - datetime.timedelta(days=30)
        _rhi = today - datetime.timedelta(days=2)
        _d = lambda v: datetime.date.fromisoformat(v[:10]) if isinstance(v, str) else v
        try:
            printed = {}
            for r in q("SELECT instrument, transaction_date, SUM(COALESCE(maturity_bdt_crore,0)) "
                       "FROM omo_transactions WHERE transaction_date BETWEEN :lo AND :hi "
                       "GROUP BY instrument, transaction_date", lo=_rlo, hi=_rhi):
                if r[2] and float(r[2]) > 0:
                    printed[(r[0], _d(r[1]))] = float(r[2])
            live = {}
            for r in q("SELECT instrument, maturity_date, accepted_bdt_crore, rate_pct, tenor_days "
                       "FROM omo_transactions WHERE accepted_bdt_crore > 0 "
                       "AND maturity_date BETWEEN :lo AND :hi", lo=_rlo, hi=_rhi):
                k = (r[0], _d(r[1]))
                p, rate, ten = float(r[2] or 0), float(r[3] or 0), int(r[4] or 0)
                live[k] = live.get(k, 0.0) + p * (1 + rate / 100 * ten / 365)
            # A tranche placed before our OMO history began (up to 180 days for
            # AR/SLS/SRF) can mature inside the window with no record of the
            # injection — that is the history boundary, not a missing release.
            hs = q("SELECT MIN(transaction_date) FROM omo_transactions")[0][0]
            hist_start = _d(hs) if hs else None
            for (inst, d), bb in sorted(printed.items(), key=lambda x: (x[0][1], x[0][0])):
                ours = live.get((inst, d), 0.0)
                if ours < bb and hist_start and d - datetime.timedelta(days=180) < hist_start:
                    continue                          # possibly pre-window — can't judge
                if abs(ours - bb) > max(0.015 * bb, 10):
                    kind = "missing/misdated injection" if ours < bb else "phantom tranche"
                    add("omo-ledger", f"{d} {inst}: tranches maturing {ours:,.0f} cr != BB printed "
                                      f"{bb:,.0f} cr — {kind}; outstanding is wrong until fixed")
        except Exception as exc:
            add("omo-ledger", f"reconciliation failed ({exc})")
        # ---- maturity principal must equal the security's outstanding face ----
        # A G-sec redeems its FULL outstanding at maturity. If a maturity event's
        # principal differs from the security's outstanding, the event was built
        # from a stale/wrong face (the id-collision class) and the redemption
        # inflow shown to the desk is wrong. Forward maturities only — past faces
        # are frozen and may predate an outstanding revision.
        for r in q("SELECT m.isin, m.scheduled_date, m.principal_bdt_mill, s.outstanding_bdt_mill "
                   "FROM maturity_events m JOIN securities s ON m.isin = s.isin "
                   "WHERE s.outstanding_bdt_mill > 0 AND m.principal_bdt_mill > 0 "
                   "AND m.payment_date >= :today", today=today):
            if abs((r[2] or 0) - (r[3] or 0)) > max(1.0, (r[3] or 0) * 0.001):
                add("maturities", f"{r[0]} {r[1]} principal {r[2]:.0f} != outstanding face {r[3]:.0f}")

        # ---- liquidity ladder must reconcile to its source events ----
        # daily_net_flow is a *materialised* aggregate that the forecast/ladder
        # trades off. If it is rebuilt from a different event snapshot than the
        # live coupon/maturity/auction rows — or bucketed on a different date key
        # — the ladder silently diverges from the drilldown the desk clicks into
        # (Aug 2026: the ladder showed a 49bn maturity and weekend-dated coupons
        # that no live event supported). Reconcile every row inside the forecast
        # window against the events bucketed by payment/settlement date — exactly
        # what build_daily_flows buckets on — so a stale ladder fails loud.
        horizon = today + datetime.timedelta(days=60)

        def _by_date(sql) -> dict:
            out: dict[str, float] = {}
            for row in q(sql, today=today, horizon=horizon):
                k = row[0]
                k = k[:10] if isinstance(k, str) else str(k)
                out[k] = out.get(k, 0.0) + (row[1] or 0.0)
            return out

        recon = (
            ("coupon",
             "SELECT flow_date, coupon_inflow_bdt_mill FROM daily_net_flow "
             "WHERE flow_date > :today AND flow_date <= :horizon",
             "SELECT payment_date, amount_bdt_mill FROM coupon_events "
             "WHERE payment_date > :today AND payment_date <= :horizon"),
            ("principal",
             "SELECT flow_date, principal_inflow_bdt_mill FROM daily_net_flow "
             "WHERE flow_date > :today AND flow_date <= :horizon",
             "SELECT payment_date, principal_bdt_mill FROM maturity_events "
             "WHERE payment_date > :today AND payment_date <= :horizon"),
            ("auction",
             "SELECT flow_date, auction_outflow_planned_mill FROM daily_net_flow "
             "WHERE flow_date > :today AND flow_date <= :horizon",
             "SELECT settlement_date, offered_amount_bdt_mill FROM auction_events "
             "WHERE settlement_date > :today AND settlement_date <= :horizon"),
        )
        for label, ladder_sql, event_sql in recon:
            ladder = _by_date(ladder_sql)
            evt = _by_date(event_sql)
            for d in sorted(set(ladder) | set(evt)):
                a, e = ladder.get(d, 0.0), evt.get(d, 0.0)
                if abs(a - e) > max(5.0, e * 0.005):
                    add("ladder-recon", f"{d} {label}: ladder {a:.0f} != events {e:.0f} mill "
                                        f"— daily_net_flow stale vs live rows; rebuild the flow pipeline")

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

        # ---- policy corridor freshness (tolerant until first fetch populates it) ----
        # The daily fetcher re-confirms the corridor from BB's homepage; if it
        # stops, the displayed policy rates could silently drift from BB again.
        try:
            pv = s.execute(text("SELECT MAX(last_seen_date) FROM policy_rate_snapshots")).scalar()
            if pv is not None:
                if isinstance(pv, str):
                    pv = datetime.date.fromisoformat(pv[:10])
                if pv < today - datetime.timedelta(days=5):
                    add("freshness", f"policy_corridor: last re-checked {pv} (>5 days) — BB policy fetcher may be broken; rates could be drifting")
        except Exception:
            pass  # table not created yet (first pipeline run makes it)

        # ---- cross-source consistency (the same fact from two feeds must agree) ----
        # The strongest kind of integrity check: when two independent BB feeds
        # describe the same rate, a disagreement means one of them is wrong —
        # exactly the class of bug (a mislabelled/mis-signed SDF, a drifted
        # corridor) that value-plausibility alone can't catch.
        try:
            pol = s.execute(text("SELECT sdf, slf FROM policy_rate_snapshots "
                                 "ORDER BY first_seen_date DESC, id DESC LIMIT 1")).fetchone()
            if pol and pol[0] is not None:
                sdf_floor = float(pol[0])
                slf_ceil = float(pol[1]) if pol[1] is not None else None
                # SDF is a FIXED-rate standing facility → its operation rate must
                # equal the corridor floor. (This would have flagged the SDF
                # mislabel had the rate also been off.)
                r = s.execute(text("SELECT transaction_date, rate_pct FROM omo_transactions "
                                   "WHERE instrument='SDF' AND rate_pct IS NOT NULL "
                                   "ORDER BY transaction_date DESC LIMIT 1")).fetchone()
                if r and r[1] is not None and abs(float(r[1]) - sdf_floor) > 0.05:
                    add("cross-source", f"SDF operation rate {r[1]}% ({r[0]}) != policy SDF floor "
                                        f"{sdf_floor}% — OMO parse or corridor is wrong")
                # Call-money O/N WAR should sit within the corridor; a WIDE bound
                # (±1pp) catches data errors (e.g. a mis-scaled rate) without
                # false-flagging genuine near-corridor moves.
                if slf_ceil is not None:
                    r = s.execute(text("SELECT trade_date, average_rate_pct FROM call_money_rates "
                                       "WHERE average_rate_pct IS NOT NULL ORDER BY trade_date DESC LIMIT 1")).fetchone()
                    if r and r[1] is not None:
                        war = float(r[1])
                        if war < sdf_floor - 1.0 or war > slf_ceil + 1.0:
                            add("cross-source", f"call-money WAR {war}% ({r[0]}) far outside corridor "
                                                f"{sdf_floor}-{slf_ceil}% — data error or extreme dislocation")
        except Exception:
            pass  # policy_rate_snapshots not populated yet

        # ---- reserves / remittance ----
        for r in q("SELECT month, gross_reserves_usd_mn, net_reserves_bpm6_usd_mn FROM reserves_monthly "
                   "WHERE gross_reserves_usd_mn IS NOT NULL AND (gross_reserves_usd_mn < 0 OR gross_reserves_usd_mn > 60000 "
                   "OR (net_reserves_bpm6_usd_mn IS NOT NULL AND net_reserves_bpm6_usd_mn > gross_reserves_usd_mn))"):
            add("reserves", f"{r[0]} gross {r[1]} / net {r[2]} implausible")
        for r in q("SELECT month, remittance_usd_mn FROM remittance_monthly "
                   "WHERE remittance_usd_mn IS NOT NULL AND (remittance_usd_mn < 0 OR remittance_usd_mn > 10000)"):
            add("remittance", f"{r[0]} remittance {r[1]} mn implausible")

        # ---- monetary indicators (real, BB econdata; DB-backed) ----
        # Plausibility bounds so a parse drift is caught: CPI/growth as %,
        # lending must exceed deposit. Wrapped — the table may not exist yet.
        try:
            for r in q("SELECT month, cpi_p2p, cpi_12mo_avg FROM monetary_monthly WHERE "
                       "(cpi_p2p IS NOT NULL AND (cpi_p2p < -5 OR cpi_p2p > 40)) OR "
                       "(cpi_12mo_avg IS NOT NULL AND (cpi_12mo_avg < -5 OR cpi_12mo_avg > 40))"):
                add("monetary", f"{r[0]} CPI implausible (p2p {r[1]}, 12mo {r[2]})")
            for r in q("SELECT month, wavg_deposit, wavg_lending FROM monetary_monthly WHERE "
                       "(wavg_deposit IS NOT NULL AND (wavg_deposit <= 0 OR wavg_deposit > 25)) OR "
                       "(wavg_lending IS NOT NULL AND (wavg_lending <= 0 OR wavg_lending > 30)) OR "
                       "(wavg_deposit IS NOT NULL AND wavg_lending IS NOT NULL AND wavg_lending < wavg_deposit)"):
                add("monetary", f"{r[0]} bank rates implausible (dep {r[1]}, lend {r[2]})")
            for r in q("SELECT month, m2_growth, private_credit_growth FROM monetary_monthly WHERE "
                       "(m2_growth IS NOT NULL AND (m2_growth < -20 OR m2_growth > 60)) OR "
                       "(private_credit_growth IS NOT NULL AND (private_credit_growth < -30 OR private_credit_growth > 60))"):
                add("monetary", f"{r[0]} growth implausible (M2 {r[1]}, pvt {r[2]})")
        except Exception:
            pass   # monetary_monthly not created yet (first pipeline run makes it)
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
