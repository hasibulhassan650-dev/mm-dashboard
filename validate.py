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


import calendar_utils as _cal
import engines.expectations as _EXPECT
import engines.absences as _absences


def _holidays_set():
    """Gazetted holidays, so a closed day is never reported as a gap.

    This read the calendar through `calendar_utils.load_calendar_rows()` —
    which takes the rows as an ARGUMENT and raises without them. The call
    therefore failed on every invocation, was swallowed by the except, and
    returned an EMPTY SET: every holiday-aware check in this file has been
    running as though Bangladesh never closes.

    It did no visible damage only because holiday_calendar itself was nearly
    empty and the few dates it held fell on Fridays. With BB's real list
    loaded, 20 and 21 October are gazetted weekday closures — so from next
    Tuesday this would have reported "a working day missing INSIDE the series"
    for every daily dataset at once, which is the false-alarm flood that
    teaches everyone to ignore the report.
    """
    from sqlalchemy import text as _t
    import calendar_utils as _c
    try:
        s = get_session()
        try:
            rows = s.execute(_t("SELECT calendar_date FROM holiday_calendar WHERE "
                                + _c.CLOSURE_SQL)).fetchall()
        finally:
            s.close()
    except Exception:
        return set()
    out = set()
    for r in rows:
        v = r[0]
        if hasattr(v, "year"):
            out.add(v)
        else:
            try:
                out.add(datetime.date.fromisoformat(str(v)[:10]))
            except ValueError:
                pass
    return out


def _empty_fetches(session) -> dict:
    """Steps whose most recent run stored nothing and said why.

    Reads the reasons the fetchers now return. A step that reports an
    empty_reason has told us it failed; before this existed the same situation
    was recorded as "OK | new_rows=0".
    """
    import json
    from sqlalchemy import text as _t
    row = session.execute(_t(
        "SELECT new_rows FROM pipeline_runs WHERE new_rows IS NOT NULL "
        "ORDER BY run_utc DESC LIMIT 1")).fetchone()
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except Exception:
        return {}
    out = {}
    for step, info in (data.items() if isinstance(data, dict) else []):
        if isinstance(info, dict) and info.get("empty_reason"):
            out[step] = info["empty_reason"]
    return out


def integrity_check(limit_per_rule: int = 8) -> dict:
    s = s_ = get_session()
    issues: list[str] = []
    by_table: dict[str, int] = {}
    waiting: list[str] = []
    by_table_waiting: dict[str, int] = {}
    notices: list[str] = []
    by_table_notice: dict[str, int] = {}

    def add(table: str, msg: str, severity: str = "defect"):
        """Record a finding.

        severity="defect"  — OUR data is wrong. Fails the build. Always.
        severity="waiting" — Bangladesh Bank has not published something yet.
                             Visible on the site, in the digest and in the issue
                             ledger with an age, but it does NOT fail the build:
                             it is not actionable and it is not a fault in our
                             data. Before this split, BB being late turned every
                             refresh red ~28x a day, which is exactly how a real
                             failure comes to look like the wallpaper.

        severity="notice"  — BB changed the SHAPE of a page and the parse is
                             still clean. Not our data being wrong and not BB
                             being late: a third thing, which is why it gets a
                             third bucket. It must be visible and dated, and it
                             must NOT fail the build — a red build for a
                             reworded header is how a real failure becomes the
                             wallpaper, which is the mistake this whole split
                             exists to undo.
        """
        bucket, counts = {
            "waiting": (waiting, by_table_waiting),
            "notice": (notices, by_table_notice),
        }.get(severity, (issues, by_table))
        counts[table] = counts.get(table, 0) + 1
        if sum(1 for i in bucket if i.startswith(table)) < limit_per_rule:
            bucket.append(f"{table}: {msg}")

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

        # ---- one auction stored twice under two tenor labels ----
        # primary_yield_snapshots is keyed (tenor_label, auction_date), and for a
        # RE-ISSUE the label has been derived two different ways over time: from
        # BB's remaining-maturity column ("2Y" on a 5-year bond with 2.6 years
        # left) and from its "Tenor and name" column ("5Y"). When the convention
        # changed, the re-parse inserted BESIDE the existing row instead of
        # updating it, so one auction exists twice.
        #
        # The ISIN plus the auction date identify the auction, and identical
        # amounts and cut-off confirm it: two genuine auctions never clear at the
        # same rate for the same size on the same day for the same security.
        # Found once, Feb-2026 BD0928101056 (labels 2Y and 5Y). It double-counts
        # in anything summing this table, including the desk's auction sheet.
        for r in q("SELECT isin, auction_date, COUNT(*) n, "
                   "COUNT(DISTINCT tenor_label) labels, "
                   "COUNT(DISTINCT cutoff_yield_pct) cuts "
                   "FROM primary_yield_snapshots WHERE isin IS NOT NULL "
                   "GROUP BY isin, auction_date HAVING COUNT(*) > 1"):
            add("yields", f"{r[0]} auctioned {r[1]} is stored {r[2]} times under {r[3]} tenor "
                          f"labels ({r[4]} distinct cut-off) — one auction, two rows; it "
                          f"double-counts wherever this table is summed")

        # ---- one auction booked twice in the ladder ----
        # BB's results page prints ONE date per auction; the other is derived
        # from it. Which one it prints was settled in Sep-2026: it is the ISSUE
        # date, and the auction is the previous working day. A parse that reads
        # that column the other way round stores the auction a day or two out —
        # and because primary_yield_snapshots is keyed (tenor_label,
        # auction_date), the wrong pairing does not overwrite the right one, it
        # lands BESIDE it. confirm_auctions_from_results then gives each row its
        # own auction_events row, and build_daily_flows sums settlement dates
        # without de-duplicating: an outflow the desk does not owe.
        #
        # 23-May-2026 is the case that exposed it — 90,000 mn of pre-Eid bills
        # booked twice. The check lives HERE, on the table the ladder reads,
        # rather than on the yields rows, because the yields table cannot tell
        # the two apart: BB really does print two auctions of one tenor days
        # apart with identical amounts and an identical cut-off (364D at 245 cr
        # and 9.5% in Dec-2011, verified against BB's page). What separates a
        # real second auction from a parse artifact is that the real one is in
        # BB's published CALENDAR. A duplicate always has an uncalendared row on
        # one side, because that row exists only because a result row did.
        _dt = lambda v: datetime.date.fromisoformat(v[:10]) if isinstance(v, str) else v
        _booked: dict = {}
        for r in q("SELECT tenor_label, auction_date, settlement_date, accepted_amount_bdt_mill, "
                   "weighted_avg_yield_pct, COALESCE(source, '') FROM auction_events "
                   "WHERE accepted_amount_bdt_mill IS NOT NULL AND weighted_avg_yield_pct "
                   "IS NOT NULL AND auction_date IS NOT NULL ORDER BY auction_date"):
            key = (r[0], r[3], r[4])
            for prev in _booked.get(key, ()):
                if abs((_dt(r[1]) - _dt(prev[0])).days) <= 7 and ("uncalendared" in r[5]
                                                                  or "uncalendared" in prev[2]):
                    add("auctions", f"{r[0]} {r[3]} mn at {r[4]}% booked twice: settles {prev[1]} "
                                    f"(auction {prev[0]}) and {r[2]} (auction {r[1]}) — one is a "
                                    f"misread of which date BB printed, and the ladder sums both")
            _booked.setdefault(key, []).append((r[1], r[2], r[5]))

        # ---- OMO rate fingerprint: a label must match the rate it printed ----
        # Every BB facility carries a signature rate: SDF = corridor floor (~7.5,
        # overnight), SLF = ceiling (~11–11.5, overnight), repo/AR = policy rate
        # (~9.5–10), IBLF = an Islamic profit RANGE or a fixed rate ≤5, CM_REPO =
        # 4.75/90D. A label that contradicts its rate is a block-attribution
        # error in the parser. Mar–Jun 2026: 55 such rows, including 13 SDF
        # ABSORPTIONS stored as AR INJECTIONS (sign errors in net liquidity) and
        # the 15-Jun IBLF stored as CB_REPO that produced the 5,703→13,622 jump.
        # Bands are tolerant to modest policy moves; a regime change needs review.
        # MLS (28D, Islamic) has no rate signature — 9.50 in Jun–Jul 2026, 5.25
        # from Aug — so it is exempt in both directions (see fetchers/omo.py).
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
            elif rate <= 5.0 and inst in ("AR", "CB_REPO", "SLF"):        want = "IBLF"
            elif 9.0 <= rate <= 10.5 and not rng and inst in ("IBLF", "SLF", "SDF"):
                want = "CB_REPO/AR"
            ok = want is None or want == inst or (want == "CB_REPO/AR" and inst in ("CB_REPO", "AR"))
            if not ok:
                add("omo", f"{d} {inst} {ten}D @{rate}%{' ('+str(rng)+')' if rng else ''} — rate fingerprint "
                           f"says {want}: instrument mislabeled (parser block-attribution error)")
        for r in q("SELECT transaction_date, instrument, tenor_days FROM omo_transactions "
                   "WHERE instrument IN ('SDF', 'SLF') AND tenor_days <> 1"):
            add("omo", f"{r[0]} {r[1]} tenor {r[2]}D — standing facilities are overnight-only")
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
        hol = _dset("SELECT calendar_date FROM holiday_calendar WHERE "
                    + _cal.CLOSURE_SQL)
        from calendar_utils import get_next_working_day, load_calendar_rows
        load_calendar_rows(q("SELECT calendar_date, holiday_type FROM holiday_calendar"))  # working weekends too
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
                if inst == "MLS":
                    # Mudaraba Liquidity Support is repaid in PARTS as remittance
                    # claims settle (Aug/Sep-2026: 1,340 placed 09-Aug came back as
                    # 83 on 25-Aug + 1,262.40 on 06-Sep = 1,345.40 = principal +
                    # profit). Per-day matching is meaningless for it.
                    continue
                ours = live.get((inst, d), 0.0)
                if ours < bb and hist_start and d - datetime.timedelta(days=180) < hist_start:
                    continue                          # possibly pre-window — can't judge
                if abs(ours - bb) > max(0.015 * bb, 10):
                    kind = "missing/misdated injection" if ours < bb else "phantom tranche"
                    add("omo-ledger", f"{d} {inst}: tranches maturing {ours:,.0f} cr != BB printed "
                                      f"{bb:,.0f} cr — {kind}; outstanding is wrong until fixed")
        except Exception as exc:
            add("omo-ledger", f"reconciliation failed ({exc})")
        # ---- duplicate-content releases (BB re-published a stale table) ----
        # Two 'as on' dates with an identical full row-set are one operation
        # stored twice: BB's "as on 04 May 2026" was the 05-Apr table verbatim.
        # The store refuses these; if one is ever in the DB, outstanding is
        # double-counting a whole day's tranches for their entire tenor.
        _sigs = {}
        for r in q("SELECT transaction_date, instrument, tenor_days, accepted_bdt_crore, "
                   "COALESCE(maturity_bdt_crore, 0) FROM omo_transactions"):
            _sigs.setdefault(_d(r[0]), []).append((r[1], int(r[2] or 0), round(float(r[3] or 0), 2), round(float(r[4] or 0), 2)))
        _seen = {}
        for dd, rs in sorted(_sigs.items()):
            key = tuple(sorted(rs))
            if len(key) >= 3 and key in _seen:
                add("omo", f"{dd} operation is a verbatim copy of {_seen[key]} — BB re-published a stale "
                           f"table; reconstruct {dd} from printed maturities (see 04-May-2026)")
            _seen.setdefault(key, dd)
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
        # The BEST outflow is the CASH: sale value when BB published it, else the
        # accepted face value, else BB's offered target. This must mirror
        # engines/outflow.py best_outflow() exactly — if it does not, the check
        # reports a mismatch on every auction for ever, and a guard that is
        # always red is a guard nobody reads.
        best_evt: dict[str, float] = {}
        for r in q("SELECT settlement_date, offered_amount_bdt_mill, accepted_amount_bdt_mill, "
                   "       outflow_status, sale_value_bdt_mill "
                   "FROM auction_events WHERE settlement_date >= :lo AND settlement_date <= :horizon",
                   lo=today - datetime.timedelta(days=60), horizon=horizon):
            k = str(r[0])[:10]
            if r[3] == "CONFIRMED" and (r[4] is not None or r[2] is not None):
                v = r[4] if r[4] is not None else r[2]
            else:
                v = r[1] or 0.0
            best_evt[k] = best_evt.get(k, 0.0) + float(v)
        ladder_best = {}
        for r in q("SELECT flow_date, auction_outflow_best_mill FROM daily_net_flow "
                   "WHERE flow_date >= :lo AND flow_date <= :horizon",
                   lo=today - datetime.timedelta(days=60), horizon=horizon):
            ladder_best[str(r[0])[:10]] = float(r[1] or 0.0)
        for d in sorted(set(ladder_best) | set(best_evt)):
            a, e = ladder_best.get(d, 0.0), best_evt.get(d, 0.0)
            if abs(a - e) > max(5.0, e * 0.005):
                add("ladder-recon", f"{d} auction-best: ladder {a:.0f} != events {e:.0f} mill "
                                    f"— confirmed accepted amounts not reflected; rebuild the flow pipeline")

        # ---- calendar vs market activity (the holiday table must match reality) ----
        # A weekday on which call money, reference rates AND OMO are all silent is
        # a bank holiday; if it is not in holiday_calendar every coupon, maturity,
        # auction settlement and OMO maturity that day is booked on a day the
        # market was shut. Conversely a listed holiday with prints is a wrong
        # seed (the FY2025-26 "approximate" Eid dates), and a Fri/Sat with prints
        # is a declared working day that must be seeded as WORKING_DAY (23-May-
        # 2026). Sep-2026 audit: 11 real closures in FY2025-26 H1 were missing.
        work_wk = _dset("SELECT calendar_date FROM holiday_calendar WHERE holiday_type = 'WORKING_DAY'")
        _clo = datetime.date(2025, 7, 1)
        _chi = today - datetime.timedelta(days=3)
        act = {}
        for tbl, col, cond in (("call_money_rates", "trade_date", ""),
                               ("ref_rates", "trade_date", ""),
                               ("omo_transactions", "transaction_date", " AND accepted_bdt_crore > 0")):
            for dd in _dset(f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} >= :lo AND {col} <= :hi{cond}",
                            lo=_clo, hi=today):
                act[dd] = act.get(dd, 0) + 1
        d = _clo
        while d <= _chi:
            weekday = d.weekday() in (0, 1, 2, 3, 6)
            if weekday and d not in hol and act.get(d, 0) == 0:
                add("calendar", f"{d} ({d:%a}) — whole money market silent (no call money, ref rates or "
                                f"OMO) but not in holiday_calendar: seed it as a closure or fix the fetchers")
            elif d in hol and act.get(d, 0) > 0:
                add("calendar", f"{d} ({d:%a}) listed as a holiday but the market printed — wrong seed")
            elif not weekday and act.get(d, 0) > 0 and d not in work_wk:
                add("calendar", f"{d} ({d:%a}) weekend with market prints — declared working day; "
                                f"seed it as WORKING_DAY or settlements roll past it")
            d += datetime.timedelta(days=1)

        # ---- payment dates must be rolled under the CURRENT calendar ----
        # Events are re-rolled every pipeline run, so a stale payment_date means
        # the holiday table changed after the last run (or the run failed). 31
        # coupons and 5 maturities were sitting on Eid/Pohela Boishakh closures.
        for tbl in ("coupon_events", "maturity_events"):
            n = 0
            for r in q(f"SELECT isin, scheduled_date, payment_date FROM {tbl} "
                       f"WHERE scheduled_date >= :lo", lo=_clo):
                sd, pd_ = _d(r[1]), _d(r[2])
                exp = get_next_working_day(sd)["result_date"]
                if pd_ != exp:
                    n += 1
                    add("events-roll", f"{tbl} {r[0]} scheduled {sd} paid {pd_} but current calendar "
                                       f"rolls to {exp} — re-run the pipeline")
        # Auction settlement: PLANNED rows = next working day after the auction;
        # CONFIRMED rows carry BB's actual issue date (may differ — that is the
        # point). Past auctions must be CONFIRMED: an unconfirmed one means the
        # result never arrived or never linked (all 267 were like this).
        for r in q("SELECT auction_date, settlement_date, tenor_label, outflow_status FROM auction_events"):
            ad, sd = _d(r[0]), _d(r[1])
            if r[3] != "CONFIRMED":
                exp = get_next_working_day(ad + datetime.timedelta(days=1))["result_date"]
                if sd != exp:
                    add("events-roll", f"auction {ad} {r[2]} settles {sd} but calendar says {exp}")
                if ad < today - datetime.timedelta(days=5):
                    add("auctions", f"{ad} {r[2]} still PLANNED {(today - ad).days} days later — BB result "
                                    f"missing or not linked (confirm_auctions_from_results)")

        # ---- auction results: date semantics ----
        # BB's results page prints the ISSUE date; auction_date is the previous
        # working day. Every row must carry both, in that order, on working days.
        for r in q("SELECT auction_date, issue_date, tenor_label FROM primary_yield_snapshots "
                   "WHERE auction_date >= :lo", lo=_clo):
            ad, idt = _d(r[0]), _d(r[1]) if r[1] else None
            if idt is None:
                add("yields", f"{ad} {r[2]} has no issue_date — fetcher regressed to storing one date")
            elif not (idt > ad):
                add("yields", f"{ad} {r[2]} issue_date {idt} not after auction_date")
            if not is_working_day(ad, hol):
                add("yields", f"{ad} ({ad:%a}) {r[2]} auction dated on a non-working day")

        # ---- OMO facility rates vs the policy corridor in force that day ----
        # 7-day+ CB Repo / AR price AT the policy repo rate; SDF at the floor;
        # SLF at the ceiling. A different rate is either a parser slip or a
        # corridor change the history table has not recorded. Overnight CB Repo
        # / AR fine-tuning is exempt: BB printed 10.00 for it while 7-day was
        # 9.50 (13/17-Aug-2026) — verified against the PDFs. The first 7 days
        # after a corridor change are exempt too: the Aug-2026 cut was "effective
        # 02 August" but BB's 02-Aug AR and 04-Aug CB Repo still printed 10.00
        # (verified); the first 9.50 print was 09-Aug.
        ph = [(_d(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in
              q("SELECT effective_date, repo, slf, sdf FROM policy_rate_history "
                "WHERE verified = TRUE ORDER BY effective_date")]
        def _pol(dd):
            cur = None
            for e in ph:
                if e[0] <= dd: cur = e
            return cur
        for r in q("SELECT transaction_date, instrument, tenor_days, rate_pct FROM omo_transactions "
                   "WHERE accepted_bdt_crore > 0 AND rate_pct IS NOT NULL AND rate_range IS NULL "
                   "AND instrument IN ('SDF','SLF','CB_REPO','AR') ORDER BY transaction_date"):
            dd, inst, ten, rate = _d(r[0]), r[1], int(r[2] or 0), float(r[3])
            p = _pol(dd)
            if not p:
                continue
            exp = {"SDF": p[3], "SLF": p[2], "CB_REPO": p[1], "AR": p[1]}[inst]
            if inst in ("CB_REPO", "AR") and ten <= 1:
                continue
            if dd - p[0] < datetime.timedelta(days=7):
                continue
            if abs(rate - exp) > 0.011:
                add("omo-policy", f"{dd} {inst} {ten}D printed {rate}% but corridor in force says "
                                  f"{exp}% — parser slip or policy_rate_history missing a change")

        # ---- label hygiene (one label per series or charts/filters split) ----
        for r in q("SELECT DISTINCT product FROM call_money_rates"):
            if r[0] not in ("Overnight", "Short Notice", "Term"):
                add("callmoney", f"unexpected product label '{r[0]}'")
        for r in q("SELECT DISTINCT rate_type, product FROM ref_rates"):
            if r[1] not in ("Overnight", "1W", "1M", "3M") or r[0] not in ("BOFR", "DOMMR"):
                add("refrate", f"unexpected series label {r[0]}/{r[1]}")
        for r in q("SELECT effective_date FROM policy_rate_history WHERE verified = FALSE"):
            add("policy", f"unverified policy_rate_history row {r[0]} — drafts must not sit beside verified circulars")

        # ---- flows on non-working days ----
        for r in q("SELECT flow_date, total_inflow_bdt_mill, auction_outflow_best_mill FROM daily_net_flow "
                   "WHERE flow_date >= :lo AND (total_inflow_bdt_mill > 0 OR auction_outflow_best_mill > 0)", lo=_clo):
            fd = _d(r[0])
            if not is_working_day(fd, hol):
                add("flows", f"{fd} ({fd:%a}) books {r[1]:.0f} in / {r[2]:.0f} out on a non-working day")

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

        # ---- interbank FX market ----
        # BDT/USD has traded 80-140 for a decade; anything outside 50-300 is a
        # parse error, not a market move. The rate-band invariant (low<=war<=high)
        # is a DB constraint, so it cannot be violated here — this catches scale.
        for r in q("SELECT trade_date, segment, war_rate, high_rate, low_rate FROM interbank_fx "
                   "WHERE war_rate IS NOT NULL AND (war_rate < 50 OR war_rate > 300)"):
            add("fxmarket", f"{r[0]} {r[1]} spot rate {r[2]} implausible for BDT/USD")
        for r in q("SELECT DISTINCT segment FROM interbank_fx"):
            if r[0] not in ("SPOT", "FORWARD", "SWAP"):
                add("fxmarket", f"unknown FX segment '{r[0]}' — BB publishes only SPOT/FORWARD/SWAP")
        # BB prints no rates for forward/swap; a rate there means the parser put
        # spot's numbers on the wrong table.
        for r in q("SELECT trade_date, segment FROM interbank_fx "
                   "WHERE segment <> 'SPOT' AND war_rate IS NOT NULL"):
            add("fxmarket", f"{r[0]} {r[1]} carries a rate — BB publishes rates for SPOT only, "
                            f"so this row was mis-attributed")
        for r in q("SELECT trade_date, segment, volume_usd_mn FROM interbank_fx "
                   "WHERE volume_usd_mn < 0"):
            add("fxmarket", f"{r[0]} {r[1]} negative turnover {r[2]}")

        # ---- published exchange rates ----
        # rate_date is the trading day, published_date the day BB put it up. If
        # they ever coincide the fetcher has stopped applying BB's own
        # "previous business day" rule and every rate is a day late.
        for r in q("SELECT rate_date, published_date, currency FROM fx_rates_daily "
                   "WHERE published_date IS NOT NULL AND published_date <= rate_date"):
            add("fxrates", f"{r[2]} {r[0]}: published {r[1]} is not after the trade date — "
                           f"the publication/trade date distinction has been lost")
        for r in q("SELECT rate_date, currency, bid_rate, ask_rate FROM fx_rates_daily "
                   "WHERE bid_rate <= 0 OR ask_rate <= 0"):
            add("fxrates", f"{r[1]} {r[0]} non-positive bid/ask {r[2]}/{r[3]}")
        _CCY = {"USD", "EUR", "GBP", "AUD", "JPY", "CAD", "SEK", "SGD", "CNH", "INR", "LKR"}
        for r in q("SELECT DISTINCT currency FROM fx_rates_daily"):
            if r[0] not in _CCY:
                add("fxrates", f"unexpected currency '{r[0]}' — BB publishes {len(_CCY)} on this page")

        # ---- interbank repo ----
        for r in q("SELECT trade_date, war_pct FROM interbank_repo "
                   "WHERE war_pct IS NOT NULL AND (war_pct <= 0 OR war_pct > 30)"):
            add("repo", f"{r[0]} repo WAR {r[1]}% out of range")
        for r in q("SELECT trade_date, amount_crore FROM interbank_repo WHERE amount_crore < 0"):
            add("repo", f"{r[0]} negative repo turnover {r[1]}")
        for r in q("SELECT trade_date, tenor_min_days, tenor_max_days FROM interbank_repo "
                   "WHERE tenor_min_days < 1 OR tenor_max_days > 365 OR tenor_min_days > tenor_max_days"):
            add("repo", f"{r[0]} implausible repo tenor range {r[1]}-{r[2]} days")

        # ---- freshness expectations (catch silent-stale failures) ----
        # A fetch that "succeeds" but stores nothing new must be reported, not
        # pass unnoticed (July 2026: FY-rollover left the auction calendar
        # seed stale → "no auctions next month" presented as fact).
        #
        # Stale data has TWO opposite causes needing opposite responses, so the
        # check asks which one it is instead of treating them alike:
        #   * our fetch ran and BB has nothing newer → waiting on BB
        #   * our fetch is failing or not running    → our defect
        #
        # The signal is "did OUR fetch step run without error", which is already
        # recorded: weekly_fetch stores per-step failures on the pipeline_runs
        # row as "<step>: <exception>". NOT ingested_utc — that only moves when
        # a row is actually written, so a source BB has not updated looks
        # un-fetched precisely in the case this has to judge (interbank repo,
        # fetched hourly, unchanged since 24-Sep, ingested_utc frozen with it).
        _run = None
        try:
            _run = s.execute(text("SELECT run_utc, errors FROM pipeline_runs "
                                  "ORDER BY run_utc DESC LIMIT 1")).fetchone()
        except Exception:
            pass

        def _fetch_is_healthy(step: str) -> bool:
            if not _run or _run[0] is None:
                return False
            ran = _run[0]
            if isinstance(ran, str):
                ran = datetime.datetime.fromisoformat(ran[:19])
            if (datetime.datetime.utcnow() - ran).total_seconds() >= 14 * 3600:
                return False                      # the refresh itself has stopped
            try:
                import json as _json
                errs = _json.loads(_run[1]) if _run[1] else []
            except Exception:
                errs = []
            return not any(str(e).startswith(f"{step}:") for e in errs)

        def _max_date(sql) -> datetime.date | None:
            v = s.execute(text(sql)).scalar()
            if isinstance(v, str):
                v = datetime.date.fromisoformat(v[:10])
            return v

        # The 5th field names the weekly_fetch STEP that feeds this dataset, so
        # a stale source can be attributed to BB or to us. None means the rule
        # is about our own computation and can never be BB's lateness
        # (flows_forward is a projection we build ourselves).
        FRESHNESS_RULES = [
            # (dataset, sql for max date, min acceptable, description, fetch step)
            ("auctions_forward", "SELECT MAX(settlement_date) FROM auction_events",
             today + datetime.timedelta(days=7),
             "no planned auction ≥7 days ahead — BB auctions T-bills weekly; calendar likely stale",
             "pipeline"),
            ("fxmarket", "SELECT MAX(trade_date) FROM interbank_fx",
             today - datetime.timedelta(days=5),
             "interbank FX turnover is published every working day by BB",
             "interbank_fx"),
            ("fxrates", "SELECT MAX(rate_date) FROM fx_rates_daily",
             today - datetime.timedelta(days=5),
             "BB publishes exchange rates every working day, describing the previous one",
             "fxrates"),
            ("repo", "SELECT MAX(trade_date) FROM interbank_repo",
             today - datetime.timedelta(days=5),
             "interbank repo is published every working day by BB",
             "interbank_repo"),
            ("secondary", "SELECT MAX(settlement_date) FROM mtm_snapshots",
             today - datetime.timedelta(days=4),
             "secondary market (GSOM) trades are published every working day",
             "pipeline"),
            ("callmoney", "SELECT MAX(trade_date) FROM call_money_rates",
             today - datetime.timedelta(days=4),
             "call money is published every working day by BB",
             "callmoney"),
            ("refrate", "SELECT MAX(trade_date) FROM ref_rates",
             today - datetime.timedelta(days=4),
             "reference rates are published every working day by BB",
             "refrate"),
            ("omo", "SELECT MAX(transaction_date) FROM omo_transactions",
             today - datetime.timedelta(days=10),
             "OMO operations are published every working day by BB",
             "omo"),
            ("yields", "SELECT MAX(auction_date) FROM primary_yield_snapshots",
             today - datetime.timedelta(days=14),
             "BB auctions bills weekly and bonds monthly, so a fortnight is the outer limit",
             "treasury"),
            ("flows_forward", "SELECT MAX(flow_date) FROM daily_net_flow",
             today + datetime.timedelta(days=30),
             "forward flow window <30 days — coupon/maturity projection broke",
             None),
        ]
        for name, sql, min_ok, desc, src_table in FRESHNESS_RULES:   # src_table = fetch step
            # The threshold comes from the dataset's declared cadence, not from
            # a number picked per rule. OMO's was 10 DAYS for a series published
            # every working day, so nothing could be reported for ten days and
            # the gap was then `waiting` forever. Forward-looking rules
            # (auctions_forward, flows_forward) check a HORIZON, not recency,
            # and keep their own threshold.
            _ds = _EXPECT.BY_KEY.get(name)
            if _ds is not None and _ds.stale_after_days and min_ok <= today:
                min_ok = today - datetime.timedelta(days=_ds.stale_after_days)
            try:
                mx = _max_date(sql)
            except Exception as exc:
                add("freshness", f"{name}: check failed ({exc})")
                continue
            if mx is None or mx < min_ok:
                fetched = _fetch_is_healthy(src_table) if src_table else False
                # WAITING EXPIRES. This is the fix for an eleven-day OMO outage
                # in which this very check fired, called the gap "implausible",
                # and still filed it as `waiting` — which never fails a build
                # and never alerts. "BB is a day late" and "BB has stopped
                # publishing" look identical in the data; only elapsed time
                # separates them, so the dataset's declared tolerance decides.
                ds = _EXPECT.BY_KEY.get(name)
                # Daily series are judged in WORKING days; counting calendar
                # days would escalate every series each weekend.
                age = ds.age(mx, today, _holidays_set()) if (ds and mx) else (
                    (today - mx).days if mx else None)
                escalated = False
                if ds is not None and age is not None:
                    escalated = ds.severity_for_age(age) == "defect"

                if fetched and not escalated:
                    add("freshness", f"{name}: BB has published nothing newer than {mx} "
                                     f"(our last successful fetch was within 14h) — {desc}", "waiting")
                elif escalated:
                    tol = ds.tolerance_days
                    add("freshness",
                        f"{name}: ESCALATED — nothing newer than {mx}, {age} "
                        f"{'working ' if ds.cadence == _EXPECT.WORKING_DAY else ''}days "
                        f"and counting. "
                        f"{ds.label} is published {ds.cadence.replace('_', ' ')} by {ds.source}, "
                        f"so a gap beyond {tol} days is ours, not BB's"
                        + (" (our fetch reports success, which means it is succeeding at "
                           "fetching nothing)" if fetched else ""))
                else:
                    add("freshness", f"{name}: latest={mx} (need ≥{min_ok}) and OUR fetch is not "
                                     f"current either — {desc}")

        # ── the guardian watches itself ─────────────────────────────────────
        # The issue ledger stopped being written and nothing noticed: two rows,
        # both closed, nine days old, while an eleven-day outage ran. An alarm
        # that can go quiet without anyone hearing is not an alarm, and it is
        # the one thing no data check can reveal, because a silent ledger looks
        # exactly like a clean one.
        # The right signal is whether the guardian RAN, not whether it wrote a
        # row: with nothing to report it correctly writes nothing, so "no issue
        # recorded lately" is indistinguishable from a clean system. Each
        # refresh stores its ledger result in pipeline_runs.quality; its
        # ABSENCE from a recent run means the guardian did not run at all.
        import json as _json
        last = s.execute(text(
            "SELECT run_utc, quality FROM pipeline_runs ORDER BY run_utc DESC LIMIT 1")).fetchone()
        if last and last[0] is not None:
            _run_at = last[0]
            if isinstance(_run_at, str):          # SQLite hands timestamps back as text
                _run_at = datetime.datetime.fromisoformat(_run_at[:19])
            age_h = (datetime.datetime.utcnow() - _run_at).total_seconds() / 3600
            if age_h < 36:
                # NOT named `q`: that is the query helper used throughout this
                # function, and shadowing it breaks every check after this one.
                last_quality = {}
                try:
                    last_quality = _json.loads(last[1]) if last[1] else {}
                except Exception:
                    last_quality = {}
                if "ledger" not in last_quality:
                    add("guardian", "the last refresh did not record a guardian ledger result "
                                    "— the guardian is not running inside the pipeline, so "
                                    "anything it would have found is lost when the run ends")
            else:
                add("guardian", f"no pipeline run in {age_h/24:.1f} days — the refresh itself "
                                f"has stopped, so every freshness check below is measuring "
                                f"data nobody is trying to update")

        # ── values that parsed and are still wrong ──────────────────────────
        # A unit change at the source is the quietest failure there is: BB
        # printing million where it printed crore leaves every figure parseable,
        # every type valid, every freshness rule green, and the whole series out
        # by a factor of ten. Only the magnitude gives it away.
        try:
            from engines.drift import findings as _drift
            for line in _drift(s_, today):
                add("drift", line)
        except Exception as exc:
            s.rollback()
            add("drift", f"the distribution check could not run ({exc}) — a unit "
                         f"change at BB would currently be invisible")

        # ── a desk mark against what BB then published ───────────────────────
        # The mark is provisional by construction and BB's figure supersedes
        # it, so a divergence is never a defect in stored data. It is still
        # worth reading: more than 10bp apart is either a typo on auction day
        # or a surprise in the auction, and the desk wants to know which one
        # it was. A mark BB has not confirmed after a week means the site is
        # still showing a provisional number on that tenor.
        try:
            for r in q("SELECT d.auction_date, d.tenor_label, d.cutoff_yield_pct, "
                       "       p.cutoff_yield_pct, d.entered_by "
                       "FROM desk_marks d LEFT JOIN primary_yield_snapshots p "
                       "  ON p.auction_date = d.auction_date "
                       " AND p.tenor_label = d.tenor_label "
                       "WHERE d.auction_date >= :lo",
                       lo=today - datetime.timedelta(days=60)):
                ad, tenor, desk_c, bb_c, who = r[0], r[1], r[2], r[3], r[4]
                if not hasattr(ad, "year"):
                    try:
                        ad = datetime.date.fromisoformat(str(ad)[:10])
                    except ValueError:
                        continue
                if bb_c is None:
                    age = _EXPECT.working_days_between(ad, today, _holidays_set())
                    if age >= 5:
                        add("desk", f"{ad} {tenor}: the desk's {desk_c}% has stood "
                                    f"unconfirmed for {age} working days — BB has not "
                                    f"published this auction, so the figure on the site "
                                    f"is still the desk's own", "waiting")
                    continue
                gap = abs(float(desk_c) - float(bb_c)) * 100.0
                if gap > 10.0:
                    add("desk", f"{ad} {tenor}: the desk marked {desk_c}% and BB published "
                                f"{bb_c}% — {gap:.1f}bp apart"
                                + (f" (entered by {who})" if who else "")
                                + ". BB's figure is the one in use; this is the desk's "
                                  "read being checked, not a data defect",
                        "notice")
        except Exception as exc:
            s.rollback()
            add("desk", f"the desk-mark check could not run ({exc})")

        # ── BB announced something and we have no record of acting on it ────
        # The listing is kept so this question can be asked at all. BB
        # announced Bangladesh's first bond buyback on 05-Oct-2026 and its
        # result on the 8th; nothing here noticed either, and 1,716.14 crore
        # reached the market on the 12th with no line in the ladder. A release
        # in a category we HANDLE with no matching event is ours to answer.
        try:
            for r in q("SELECT serial, published_date, category, title FROM press_releases "
                       # LOWER(...) LIKE, not ILIKE: ILIKE is Postgres-only and
                       # this check then ran nowhere else, including its tests.
                       "WHERE category = 'BUYBACK' "
                       "AND LOWER(title) NOT LIKE '%notice%' "
                       "ORDER BY published_date DESC LIMIT 12"):
                serial, pub, _cat, title = r[0], r[1], r[2], r[3]
                if not hasattr(pub, "year"):
                    try:
                        pub = datetime.date.fromisoformat(str(pub)[:10])
                    except ValueError:
                        continue
                near = q("SELECT COUNT(*) FROM buyback_events "
                         "WHERE settlement_date BETWEEN :lo AND :hi",
                         lo=pub - datetime.timedelta(days=3),
                         hi=pub + datetime.timedelta(days=21))
                if not near or not near[0][0]:
                    add("press", f"{serial} ({pub}) announces a BUYBACK and no buyback event "
                                 f"is recorded within three weeks of it — cash moved and the "
                                 f"ladder does not know: \"{(title or '')[:70]}\"")
            # BGIIB is published weekly and nothing stores it yet. Stated once,
            # with a count, rather than per release: a true gap in coverage is
            # not the same as a defect in data we hold, and pretending
            # otherwise would put thirteen identical lines in every report.
            bg = q("SELECT COUNT(*), MAX(published_date) FROM press_releases "
                   "WHERE category IN ('BGIIB', 'SUKUK')")
            if bg and bg[0][0]:
                add("press", f"{bg[0][0]} Islamic-window release(s) on record (latest "
                             f"{bg[0][1]}) and no BGIIB dataset stores their figures — the "
                             f"releases are captured, the auction results are not",
                    "notice")
        except Exception as exc:
            s.rollback()
            add("press", f"the announcement-vs-event check could not run ({exc})")

        # ── a buyback must agree with GSOM's outstanding ────────────────────
        # Two independent BB publications state the same fact: the press
        # release gives the accepted amount, and GSOM's daily table gives the
        # outstanding, which must fall by exactly that much on settlement. That
        # is how 08-Oct-2026's figure was established as FACE value and its
        # settlement date as 12-Oct — the release said neither. A buyback typed
        # in by hand, or a release we misread, fails here rather than silently
        # moving 1,716 crore of liquidity to the wrong day.
        try:
            for b in q("SELECT isin, settlement_date, accepted_face_crore, cash_paid_crore "
                       "FROM buyback_events ORDER BY settlement_date"):
                isin, sd, face, cash = b[0], b[1], b[2], b[3]
                if not hasattr(sd, "year"):
                    try:
                        sd = datetime.date.fromisoformat(str(sd)[:10])
                    except ValueError:
                        continue
                pre = q("SELECT outstanding_bdt_mill FROM mtm_snapshots WHERE isin = :i "
                        "AND settlement_date < :d AND outstanding_bdt_mill IS NOT NULL "
                        "ORDER BY settlement_date DESC LIMIT 1", i=isin, d=sd)
                post = q("SELECT outstanding_bdt_mill FROM mtm_snapshots WHERE isin = :i "
                         "AND settlement_date >= :d AND outstanding_bdt_mill IS NOT NULL "
                         "ORDER BY settlement_date LIMIT 1", i=isin, d=sd)
                if not pre or not post:
                    add("buyback", f"{isin} buyback settling {sd} cannot be checked against "
                                   f"GSOM: no outstanding figure on one side of the date",
                        "waiting")
                    continue
                drop = float(pre[0][0]) - float(post[0][0])
                want = float(face or 0) * 10.0        # 1 crore = 10 million
                if abs(drop - want) > max(1.0, want * 0.001):
                    add("buyback", f"{isin}: BB's release says {face:,.2f} crore was bought "
                                   f"back ({want:,.1f} mill) but GSOM's outstanding fell "
                                   f"{drop:,.1f} mill across {sd} — one of the two is "
                                   f"misread, and the ladder is moving the wrong amount")
                if cash is not None and face and cash < face:
                    add("buyback", f"{isin}: cash paid {cash:,.2f} crore is below the "
                                   f"{face:,.2f} crore face retired — possible, but only on "
                                   f"a discount bond; check the release")
        except Exception as exc:
            s.rollback()
            add("buyback", f"the buyback-vs-GSOM check could not run ({exc})")

        # ── the ladder's buyback line must match the events ─────────────────
        try:
            led = {str(r[0])[:10]: float(r[1] or 0) for r in q(
                "SELECT flow_date, buyback_inflow_bdt_mill FROM daily_net_flow "
                "WHERE buyback_inflow_bdt_mill > 0")}
            evt: dict = {}
            for r in q("SELECT settlement_date, accepted_face_crore, cash_paid_crore "
                       "FROM buyback_events"):
                k = str(r[0])[:10]
                crore = r[2] if r[2] is not None else (r[1] or 0)
                evt[k] = evt.get(k, 0.0) + float(crore) * 10.0
            for d in sorted(set(led) | set(evt)):
                a, e = led.get(d, 0.0), evt.get(d, 0.0)
                if abs(a - e) > max(1.0, e * 0.001):
                    add("ladder-recon", f"{d} buyback: ladder {a:,.1f} != events {e:,.1f} "
                                        f"mill — rebuild the flow pipeline")
        except Exception:
            s.rollback()

        # ── a DERIVED OMO maturity must not land on a closure ───────────────
        # fetchers/omo.py computes a deal's maturity as txn + tenor, rolled to
        # the next working day, AT WRITE TIME — so every row written before the
        # calendar learned about a holiday still points at it. Reading BB's
        # holiday list on 15-Oct-2026 exposed three 7-day tranches dealt 13-14
        # Oct, 15,464.92 crore in all, shown as returning on the two Durga Puja
        # days when the banks are shut. Maturity-only lines are exempt: their
        # date is BB's own printed statement of what expired, not ours to move.
        try:
            hol_now = _holidays_set()
            work_wknd = _dset("SELECT calendar_date FROM holiday_calendar "
                              "WHERE holiday_type = 'WORKING_DAY'")
            for r in q("SELECT transaction_date, maturity_date, instrument, "
                       "       accepted_bdt_crore FROM omo_transactions "
                       "WHERE accepted_bdt_crore > 0 AND maturity_date >= :lo",
                       lo=today - datetime.timedelta(days=120)):
                m = r[1]
                if not hasattr(m, "year"):
                    # Raw SQL hands dates back as strings on SQLite and as
                    # dates on Postgres. Skipping the strings made this check
                    # pass vacuously everywhere except production.
                    try:
                        m = datetime.date.fromisoformat(str(m)[:10])
                    except ValueError:
                        continue
                shut = (m in hol_now) or (m.weekday() in (4, 5) and m not in work_wknd)
                if shut:
                    add("omo", f"{r[2]} {r[3]:,.2f} cr dealt {r[0]} is shown maturing "
                               f"{m} ({m.strftime('%A')}), a day the market is shut — "
                               f"the derived maturity predates the calendar entry and "
                               f"must roll to the next working day")
        except Exception as exc:
            s.rollback()
            add("omo", f"the OMO maturity-vs-calendar check could not run ({exc})")

        # ── the calendar must reach past the dates we compute ───────────────
        # holiday_calendar is the one table whose value is entirely in the
        # FUTURE: settlement dates, maturity rolls, auction dates and every
        # working-day age are derived from it. On 15-Oct-2026 it ended 26-Aug
        # and nothing had noticed, so the following Tuesday — Durga Puja — was
        # being treated as a working day. "It has rows" was never the question.
        try:
            cal_max = _max_date("SELECT MAX(calendar_date) FROM holiday_calendar "
                                "WHERE " + _cal.CLOSURE_SQL)
        except Exception:
            s.rollback()
            cal_max = None
        if cal_max is None:
            add("calendar", "holiday_calendar has no closures at all — every settlement "
                            "date is being computed as if the market never shuts")
        else:
            ahead = (cal_max - today).days
            if ahead < 0:
                add("calendar", f"holiday_calendar stops at {cal_max}, {-ahead} day(s) in the "
                                f"PAST — every date derived from today onward assumes the "
                                f"market only ever shuts at weekends")
            elif ahead < 45:
                # BB publishes the next year's list in late December, so a
                # thinning horizon in November is BB's cadence, not our bug —
                # until it runs out, which is a defect by the rule above.
                add("calendar", f"holiday_calendar reaches only {cal_max} ({ahead} days out) "
                                f"— coupons and settlements beyond it assume weekends are the "
                                f"only closures. BB posts the next list in late December",
                    "waiting")

        # ── did BB change the SHAPE of a page? ──────────────────────────────
        # Everything else here notices data that stopped arriving. This notices
        # the source moving, which is the failure that produces plausible WRONG
        # numbers rather than missing ones — and the one no freshness rule can
        # ever see, because nothing is late and nothing is empty.
        #
        # Two tiers, and the distinction is the whole point: a reworded header
        # that still parses is a dated notice, while a change that costs rows
        # is a defect. Treating them alike in either direction is fatal — all
        # red, and the alarm becomes wallpaper; all quiet, and we silently
        # publish BB's new layout read through the old parser.
        try:
            srows = q("SELECT source, verdict, reasons, last_seen, selector, "
                      "       column_count, seen_count "
                      "FROM source_snapshots WHERE verdict <> 'ok' "
                      "ORDER BY last_seen DESC")
        except Exception:
            s.rollback()          # table not migrated yet on this DB
            srows = []
        import json as _sj
        seen_sources = set()
        for src, verdict, reasons, last_seen, selector, ncols, seen_count in srows:
            # Only the CURRENT shape of each source: a change recorded weeks ago
            # and since settled is history, not news.
            if src in seen_sources:
                continue
            seen_sources.add(src)
            if last_seen is None or (datetime.datetime.utcnow() - last_seen).days > 4:
                continue
            try:
                why = "; ".join(_sj.loads(reasons) or []) if reasons else ""
            except Exception:
                why = str(reasons)
            if verdict == "degraded":
                add("source", f"{src}: BB's page changed AND the parse got worse — {why}. "
                              f"Figures from this source are suspect until the parser is "
                              f"checked against the new layout")
            else:
                add("source", f"{src}: BB's page changed but the parse is still clean — "
                              f"{why} (selector {selector or '-'}, {ncols} columns, "
                              f"seen {seen_count}x). Read by header text, so the figures "
                              f"line up; confirm the new layout means what it says",
                    "notice")

        # ── a step that ran and wrote NOTHING is not a healthy step ──────────
        # Judging a fetch by "did it raise?" cannot tell "BB published nothing"
        # from "we are broken" — and that is exactly the pair that mattered.
        # run_omo_fetch now reports an empty_reason; anything that does is a
        # defect regardless of how clean the run looked.
        for step, reason in _empty_fetches(s_).items():
            add("freshness", f"{step}: the fetch step ran and stored NOTHING — {reason}")

        # ── gaps INSIDE a series ────────────────────────────────────────────
        # Staleness only inspects the newest row, so a working day missing from
        # the middle is invisible to it. interbank_repo was missing 27-30 Sep
        # inside its own range while its latest row looked current.
        for ds in _EXPECT.DATASETS:
            # Only daily series: a "missing working day" is meaningless for a
            # monthly series (whose date column is a YYYY-MM string) or for an
            # event series where silence is normal.
            if not ds.gap_check or ds.cadence != _EXPECT.WORKING_DAY:
                continue
            try:
                rows = q(f"SELECT DISTINCT {ds.date_column} FROM {ds.table} "
                         f"WHERE {ds.date_column} >= :since", since=today - datetime.timedelta(days=45))
            except Exception:
                # Roll back, or the aborted transaction poisons every check
                # after this one and the report silently truncates.
                s.rollback()
                continue
            present = {r[0] if hasattr(r[0], "year") else None for r in rows}
            present.discard(None)
            if not present:
                continue
            gaps = _EXPECT.missing_working_days(
                present, max(min(present), today - datetime.timedelta(days=45)),
                max(present), holidays=_holidays_set())
            # Days BB itself does not publish, each established by re-reading
            # BB's own page over a window that straddles the date. Skipped here
            # so one permanent hole does not sit red for ever; counted below so
            # it never becomes invisible. Recorded only from evidence — see
            # engines/absences.py — so a fetch we failed to make is still ours.
            known = _absences.absent_dates(s_, ds.key)
            gaps = [g for g in gaps if g not in known]
            if gaps:
                shown = ", ".join(str(g) for g in gaps[:6])
                more = f" (+{len(gaps) - 6} more)" if len(gaps) > 6 else ""
                add("freshness", f"{ds.key}: {len(gaps)} working day(s) missing INSIDE the "
                                 f"series — {shown}{more}")

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

        # Two BB pages, one fact: the published USD bid/ask/WAR for a trading day
        # IS the interbank spot low/high/WAR for that day (verified to the paisa
        # on 28-Sep-2026: 122.75 / 122.85 / 122.81 on both). A disagreement means
        # one of the two parsers has drifted — including the date rule that says
        # the exchange-rate page describes the PREVIOUS business day.
        try:
            for r in q("""SELECT f.rate_date, f.bid_rate, f.ask_rate, f.war_rate,
                                 i.low_rate, i.high_rate, i.war_rate
                          FROM fx_rates_daily f
                          JOIN interbank_fx i ON i.trade_date = f.rate_date AND i.segment = 'SPOT'
                          WHERE f.currency = 'USD' AND f.rate_date >= :lo
                            AND i.war_rate IS NOT NULL AND f.war_rate IS NOT NULL""",
                       lo=today - datetime.timedelta(days=45)):
                for label, a, b in (("bid vs spot low", r[1], r[4]),
                                    ("ask vs spot high", r[2], r[5]),
                                    ("WAR", r[3], r[6])):
                    if a is not None and b is not None and abs(float(a) - float(b)) > 0.0051:
                        add("cross-source", f"{r[0]} USD {label}: exchange-rate page {a} vs interbank "
                                            f"spot {b} — the two BB pages disagree about the same day")
        except Exception:
            pass   # one of the two tables not populated yet

        # Repo vs call money. BB's repo tenor is a 1-7 day RANGE, so trading a
        # little ABOVE overnight call is normal term premium (observed +1 to
        # +13bp) — this is a dislocation check, not a "secured must be cheaper"
        # rule, so the band is deliberately wide.
        try:
            for r in q("""SELECT r.trade_date, r.war_pct, c.average_rate_pct
                          FROM interbank_repo r
                          JOIN call_money_rates c ON c.trade_date = r.trade_date
                                                 AND c.product = 'Overnight'
                          WHERE r.trade_date >= :lo""",
                       lo=today - datetime.timedelta(days=45)):
                gap = float(r[1]) - float(r[2])
                if abs(gap) > 2.0:
                    add("cross-source", f"{r[0]} repo WAR {r[1]}% vs call o/n {r[2]}% "
                                        f"({gap:+.2f}pp) — parse error or a real dislocation")
        except Exception:
            pass

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

    # `ok` / `issues` / `by_table` keep their existing meaning — OUR defects —
    # so the gate and every current consumer react to exactly what they always
    # did. Waiting-on-BB travels beside them, never inside them.
    return {
        "ok": len(by_table) == 0,
        "issue_count": sum(by_table.values()),
        "issues": issues,
        "by_table": by_table,
        "waiting": waiting,
        "waiting_count": sum(by_table_waiting.values()),
        "by_table_waiting": by_table_waiting,
        # Source-shape changes that still parse: not a defect, not BB being
        # late, and not something to discover months later in a diff.
        "notices": notices,
        "notice_count": sum(by_table_notice.values()),
        "by_table_notice": by_table_notice,
    }


if __name__ == "__main__":
    import json
    rep = integrity_check()
    print(json.dumps(rep, indent=2, default=str))
