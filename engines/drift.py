"""Values that parsed cleanly and are still wrong.

The quietest failure at a source is not a renamed column or a dead link. It is
a unit change. BB prints crore; if a page ever starts printing million, every
figure still parses, every type check passes, every freshness rule is green,
the row count is normal — and the whole series is out by a factor of ten.
Nothing else in this project can see that, because nothing else looks at the
MAGNITUDE of what arrived.

So this compares each watched field against its own history:

  * a recent median an order of magnitude away from the long-run median is a
    unit or scale change (crore <-> million, percent <-> fraction, BDT/USD
    inverted);
  * a date that parses but lands outside any plausible horizon is a format
    change (a 20-year bond maturing in 2045 is ordinary; an OMO repo maturing
    in 2045 is a parsed `20/26` read as a year).

Deliberately blunt. Markets move, and a check that fires when call money goes
from 9% to 11% is worse than no check at all — it teaches the desk to ignore
the report. These thresholds are set to catch factors of ten, not opinions.
Each field says what a WRONG value would look like, in the comment beside it,
because a threshold with no stated failure is a number nobody can revise.
"""
import datetime
import logging
from typing import List, Tuple

log = logging.getLogger("drift")

# (table, column, date column, factor, floor, note)
#
# `factor`: how far the recent median may sit from the historical median before
#   it is reported. 6x is comfortably above any real market move in these
#   series and comfortably below the 10x of a crore/million switch.
# `floor`: ignore the field until history has this many rows, so a new series
#   is never judged against three data points.
WATCHED: Tuple[tuple, ...] = (
    ("omo_transactions", "accepted_bdt_crore", "transaction_date", 6.0, 60,
     "BB prints OMO volumes in crore; in million every number is 10x"),
    ("primary_yield_snapshots", "accepted_bdt_crore", "auction_date", 6.0, 60,
     "accepted face value, crore"),
    ("primary_yield_snapshots", "sale_value_bdt_crore", "auction_date", 6.0, 60,
     "the cash actually paid, crore — this one feeds the cash ladder"),
    ("primary_yield_snapshots", "cutoff_yield_pct", "auction_date", 4.0, 60,
     "a percent printed as a fraction is 100x out and still inside 0-30"),
    ("call_money_rates", "average_rate_pct", "trade_date", 4.0, 60,
     "call money in percent"),
    ("call_money_rates", "amount_crore", "trade_date", 6.0, 60,
     "turnover, crore"),
    ("interbank_repo", "war_pct", "trade_date", 4.0, 40,
     "weighted average repo rate, percent"),
    ("interbank_repo", "amount_crore", "trade_date", 6.0, 40,
     "repo turnover, crore"),
    ("fx_rates_daily", "bid_rate", "rate_date", 4.0, 40,
     "BDT per USD ~122; inverted it is 0.008"),
    ("interbank_fx", "war_rate", "trade_date", 4.0, 40,
     "BDT per USD on the interbank market"),
    ("interbank_fx", "volume_usd_mn", "trade_date", 8.0, 40,
     "USD million — the one BB page that is NOT in crore"),
    ("ref_rates", "rate_pct", "trade_date", 4.0, 40,
     "reference rates, percent"),
    ("mtm_snapshots", "market_yield_pct", "settlement_date", 4.0, 200,
     "secondary market yield, percent"),
)

# (table, date column, earliest plausible, years ahead allowed, note)
DATE_HORIZONS: Tuple[tuple, ...] = (
    ("omo_transactions", "maturity_date", 0, 2,
     "an OMO tranche is overnight to a few months; years out means a date was "
     "misread"),
    ("primary_yield_snapshots", "issue_date", 0, 1,
     "an issue date in the future is a day/month swap"),
)

RECENT_DAYS = 45


def _median(vals: List[float]) -> float:
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return 0.0
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def findings(session, today: datetime.date = None) -> List[str]:
    """Report fields whose recent values left their own distribution."""
    from sqlalchemy import text
    today = today or datetime.date.today()
    cut = today - datetime.timedelta(days=RECENT_DAYS)
    out: List[str] = []

    for table, col, datecol, factor, floor, note in WATCHED:
        try:
            rows = session.execute(text(
                f"SELECT {datecol}, {col} FROM {table} "
                f"WHERE {col} IS NOT NULL AND {col} > 0"), {}).fetchall()
        except Exception:
            session.rollback()          # column or table absent on this DB
            continue
        hist, recent = [], []
        for d, v in rows:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            dd = d if hasattr(d, "year") else None
            if dd is None:
                try:
                    dd = datetime.date.fromisoformat(str(d)[:10])
                except ValueError:
                    continue
            (recent if dd >= cut else hist).append(fv)
        if len(hist) < floor or not recent:
            continue
        hm, rm = _median(hist), _median(recent)
        if hm <= 0:
            continue
        ratio = rm / hm
        if ratio >= factor or ratio <= 1.0 / factor:
            out.append(
                f"{table}.{col}: the last {RECENT_DAYS} days sit at a median of "
                f"{rm:,.4g} against {hm:,.4g} for the previous {len(hist):,} rows "
                f"— {ratio:,.2g}x. A number that parses and is still wrong is what "
                f"a unit change looks like ({note}). Check BB's page before "
                f"trusting anything downstream of this field.")

    for table, col, _back, years_ahead, note in DATE_HORIZONS:
        limit = today + datetime.timedelta(days=int(365.25 * years_ahead))
        try:
            rows = session.execute(text(
                f"SELECT COUNT(*), MAX({col}) FROM {table} WHERE {col} > :lim"),
                {"lim": str(limit)}).fetchone()
        except Exception:
            session.rollback()
            continue
        if rows and rows[0]:
            out.append(f"{table}.{col}: {rows[0]} row(s) dated as late as {rows[1]} "
                       f"— beyond {years_ahead}y from today. {note}")
    return out
