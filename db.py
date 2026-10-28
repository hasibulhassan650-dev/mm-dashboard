"""
db.py — SQLite schema creation and session factory.
All tables derived from the canonical schema in the blueprint.
"""
import logging
import os
from pathlib import Path
from sqlalchemy import (
    create_engine, Column, String, Date, DateTime, Integer,
    Float, Boolean, Text, UniqueConstraint, event, text
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.engine import Engine
from config import DB_URL, DB_PATH

log = logging.getLogger(__name__)
Base = declarative_base()

# ── SQLite pragmas ─────────────────────────────────────────────────────────────
# WAL mode is skipped on Streamlit Cloud (/tmp filesystem does not support it).
# foreign_keys=ON is always applied.
_IS_CLOUD = os.environ.get("HOME") == "/home/adminuser"

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    # Pragmas only apply to SQLite; skip for PostgreSQL
    if "psycopg" in type(dbapi_conn).__module__:
        return
    cursor = dbapi_conn.cursor()
    try:
        if not _IS_CLOUD:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    except Exception as exc:
        log.warning("Could not set SQLite pragma: %s", exc)
    finally:
        cursor.close()

# ── ORM Models ────────────────────────────────────────────────────────────────

class Security(Base):
    __tablename__ = "securities"
    isin                    = Column(String(12), primary_key=True)
    security_name_raw       = Column(String(80))
    security_name_norm      = Column(String(80))
    security_type           = Column(String(10))
    issue_date              = Column(Date)
    maturity_date           = Column(Date)
    coupon_rate_pct         = Column(Float)
    coupon_frequency        = Column(String(6))
    issue_price             = Column(Float)
    outstanding_bdt_mill    = Column(Float)
    source_page             = Column(String(200))
    source_settlement_date  = Column(Date)
    data_quality            = Column(String(50), default="OK")
    last_updated_utc        = Column(DateTime)


class MtmSnapshot(Base):
    __tablename__ = "mtm_snapshots"
    __table_args__ = (UniqueConstraint("isin", "settlement_date"),)
    id                      = Column(Integer, primary_key=True, autoincrement=True)
    isin                    = Column(String(12))
    settlement_date         = Column(Date)
    yield_date              = Column(Date)
    market_yield_pct        = Column(Float)
    market_price            = Column(Float)
    outstanding_bdt_mill    = Column(Float)
    remaining_maturity_raw  = Column(String(20))
    remaining_maturity_val  = Column(Float)
    remaining_maturity_unit = Column(String(5))
    last_coupon_date        = Column(Date)
    next_coupon_date        = Column(Date)
    issue_date_raw          = Column(String(20))
    maturity_date_raw       = Column(String(20))
    source_page             = Column(String(200))
    source_row_index        = Column(Integer)
    data_quality            = Column(String(50), default="OK")
    ingested_utc            = Column(DateTime)


class CouponEvent(Base):
    __tablename__ = "coupon_events"
    __table_args__ = (UniqueConstraint("isin", "scheduled_date"),)
    id                        = Column(Integer, primary_key=True, autoincrement=True)
    isin                      = Column(String(12))
    scheduled_date            = Column(Date)
    payment_date              = Column(Date)
    amount_bdt_mill           = Column(Float)
    coupon_rate_used_pct      = Column(Float)
    outstanding_used_bdt_mill = Column(Float)
    outstanding_snapshot_date = Column(Date)
    period_days_actual        = Column(Integer)
    calc_method               = Column(String(20))
    formula_string            = Column(String(200))
    is_derived                = Column(Boolean, default=True)
    is_short_coupon           = Column(Boolean, default=False)
    data_quality              = Column(String(50), default="OK")


class MaturityEvent(Base):
    __tablename__ = "maturity_events"
    __table_args__ = (UniqueConstraint("isin"),)
    id                        = Column(Integer, primary_key=True, autoincrement=True)
    isin                      = Column(String(12))
    scheduled_date            = Column(Date)
    payment_date              = Column(Date)
    principal_bdt_mill        = Column(Float)
    outstanding_snapshot_date = Column(Date)
    roll_days                 = Column(Integer, default=0)
    roll_reason               = Column(Text)
    calc_method               = Column(String(20), default="PRINCIPAL")
    formula_string            = Column(String(200))
    data_quality              = Column(String(50), default="OK")


class AuctionEvent(Base):
    __tablename__ = "auction_events"
    __table_args__ = (UniqueConstraint("fiscal_year", "auction_no", "tenor_label", "auction_date"),)
    id                        = Column(Integer, primary_key=True, autoincrement=True)
    fiscal_year               = Column(String(7))
    auction_no                = Column(String(20))   # String: bills="44", bonds="B38"
    auction_date              = Column(Date)
    settlement_date           = Column(Date)
    security_type             = Column(String(10))
    tenor_label               = Column(String(10))
    offered_amount_bdt_crore  = Column(Float)
    offered_amount_bdt_mill   = Column(Float)
    accepted_amount_bdt_crore = Column(Float)
    accepted_amount_bdt_mill  = Column(Float)
    # The CASH the market actually paid, which is not the accepted face value
    # above. Bills sell at a discount and a premium re-issue sells above par:
    # across 2026's 172 auctions the two differ on every single one, and
    # booking face value overstated the drain on T-Bills alone by 15,861 crore
    # over the year. Face value is the par amount BB allotted; this is the
    # money that left the banking system on settlement day. Blank for auctions
    # stored before the parser kept it — never substituted with face value,
    # because a figure that is right to the taka and a figure that is close are
    # different things and the ladder should say which it has.
    sale_value_bdt_crore      = Column(Float)
    sale_value_bdt_mill       = Column(Float)
    weighted_avg_yield_pct    = Column(Float)
    resulting_isin            = Column(String(12))
    outflow_status            = Column(String(12), default="PLANNED")
    roll_days                 = Column(Integer, default=0)
    roll_reason               = Column(Text)
    source                    = Column(String(300))
    data_quality              = Column(String(50), default="OK")


class PrimaryYieldSnapshot(Base):
    __tablename__ = "primary_yield_snapshots"
    __table_args__ = (UniqueConstraint("tenor_label", "auction_date"),)
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date      = Column(Date, nullable=False)
    # auction_date = the day bids were taken (T). BB's treasury results page
    # prints only the ISSUE date (T+1 working day) — that is kept in issue_date
    # and auction_date is derived as the previous working day. Until Sep-2026
    # the issue date was stored AS the auction date, so every auction sat one
    # working day late and never matched the auction calendar.
    auction_date       = Column(Date)
    issue_date         = Column(Date)
    security_type      = Column(String(10))
    tenor_label        = Column(String(15))
    tenor_years        = Column(Float)
    cutoff_yield_pct   = Column(Float)
    offered_bdt_crore  = Column(Float)     # bids RECEIVED (face value)
    accepted_bdt_crore = Column(Float)     # bids ACCEPTED, FACE value (par)
    # ── what BB prints and we previously discarded ───────────────────────────
    # BB's results table carries the ISIN, a re-issuance marker inside the same
    # cell, and a SALE value distinct from the accepted face value. All nullable
    # on purpose: the older BB layouts have fewer columns, and a 0 here would
    # read as "no bids" or "sold at par" rather than "not published".
    isin                 = Column(String(12))
    # True when BB marks the auction "(Re-issuance: N Yr.)" — the only place a
    # re-issue is flagged, and why one ISIN legitimately has several issue
    # dates with different cut-offs. The unique key stays
    # (tenor_label, auction_date); ISIN must never become the key.
    is_reissue           = Column(Boolean, default=False)
    reissue_remaining    = Column(String(20))
    # tenor_label holds BB's REMAINING maturity (a re-issued 20Y with 2.73 years
    # left is '2Y'), which is right for the curve. This is what the security
    # actually is, which is what an instrument listing must show.
    original_tenor_label = Column(String(15))
    original_tenor_years = Column(Float)
    # The CASH the market paid. On a premium re-issue it differs materially
    # from the accepted face value (02-Sep-2026 20Y: face 3,500.00, sale
    # 3,689.28) and only this is a cash flow.
    sale_value_bdt_crore = Column(Float)
    bids_received_count  = Column(Integer)
    bids_accepted_count  = Column(Integer)
    # Kept verbatim ("8.7280-11.7100"): two numbers, and collapsing them to one
    # would invent a figure BB did not print.
    yield_range_received = Column(String(40))
    yield_range_accepted = Column(String(40))
    weighted_avg_price   = Column(Float)
    devolvement_yield_pct = Column(Float)
    source             = Column(String(300))
    ingested_utc       = Column(DateTime)


class OMOTransaction(Base):
    """One row = one OMO instrument/tenor line from a BB press release PDF.
    Rows with accepted_bdt_crore=0 are maturity-only lines (no new transaction today).
    """
    __tablename__ = "omo_transactions"
    __table_args__ = (UniqueConstraint("transaction_date", "instrument", "tenor_days", "accepted_bdt_crore"),)
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    transaction_date    = Column(Date, nullable=False)
    maturity_date       = Column(Date)              # txn_date + tenor_days; txn_date if accepted=0
    instrument          = Column(String(30), nullable=False)  # CB_REPO, SLF, IBLF, AR, SDF
    tenor_label         = Column(String(10))        # "7D", "14D", "1D", "28D"
    tenor_days          = Column(Integer)
    accepted_bdt_crore  = Column(Float)             # 0 for maturity-only rows
    maturity_bdt_crore  = Column(Float)             # what matured today for this instrument/tenor (from PDF)
    rate_pct            = Column(Float)             # lower bound when a range
    rate_range          = Column(String(30))        # full range as published, e.g. "4.00-5.25"; NULL when a single rate
    direction           = Column(String(20))        # INJECTION or ABSORPTION
    source_pdf          = Column(String(300))
    source_pub_date     = Column(Date)               # press-release publication date ("Date: DD-MM-YYYY")
    source_serial       = Column(String(40))         # "Serial No- 05/2026-343" — orders corrections
    ingested_utc        = Column(DateTime)


class FxAuctionResult(Base):
    __tablename__ = "fx_auction_results"
    __table_args__ = (UniqueConstraint("auction_date", "auction_type"),)
    id                       = Column(Integer, primary_key=True, autoincrement=True)
    auction_date             = Column(Date, nullable=False)
    settlement_date          = Column(Date)
    auction_type             = Column(String(10))
    num_bids                 = Column(Integer)
    bid_amount_usd_mill      = Column(Float)
    bid_range                = Column(String(30))
    num_accepted             = Column(Integer)
    accepted_amount_usd_mill = Column(Float)
    cutoff_rate              = Column(Float)
    weighted_avg_rate        = Column(Float)
    ingested_utc             = Column(DateTime)


class InterbankFx(Base):
    """One trading day of one interbank FX segment (SPOT / FORWARD / SWAP).

    BB prints rates only for SPOT; forward and swap report turnover alone, so
    their rate columns stay NULL rather than inheriting the spot rate.
    """
    __tablename__ = "interbank_fx"
    __table_args__ = (UniqueConstraint("trade_date", "segment"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    trade_date     = Column(Date, nullable=False)
    segment        = Column(String(10))        # SPOT | FORWARD | SWAP
    num_deals      = Column(Integer)
    volume_usd_mn  = Column(Float)
    high_rate      = Column(Float)             # SPOT only
    low_rate       = Column(Float)             # SPOT only
    war_rate       = Column(Float)             # SPOT only — the market mid
    ingested_utc   = Column(DateTime)


class FxRateDaily(Base):
    """BB's published exchange rate of the Taka, one row per currency per day.

    rate_date is the day the rates DESCRIBE; published_date is the day BB put
    them up. They differ by one business day (BB's own note: "Dhaka close on
    the previous business day"), and conflating them would repeat the
    auction-date/issue-date error the Sep-2026 audit had to unwind.
    Mid = (bid + ask) / 2 is computed at read time, never stored.
    """
    __tablename__ = "fx_rates_daily"
    __table_args__ = (UniqueConstraint("rate_date", "currency"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    rate_date      = Column(Date, nullable=False)
    published_date = Column(Date)
    currency       = Column(String(3))
    bid_rate       = Column(Float)
    ask_rate       = Column(Float)
    war_rate       = Column(Float)             # USD only
    ingested_utc   = Column(DateTime)


class InterbankRepo(Base):
    """One trading day of the interbank (secured) repo market.

    BB publishes tenor and rate as RANGES ("1-7", "8.50-8.95"), so both bounds
    are stored explicitly instead of being flattened to one number.
    """
    __tablename__ = "interbank_repo"
    __table_args__ = (UniqueConstraint("trade_date"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    trade_date     = Column(Date, nullable=False)
    num_deals      = Column(Integer)
    amount_crore   = Column(Float)
    tenor_min_days = Column(Integer)
    tenor_max_days = Column(Integer)
    rate_min_pct   = Column(Float)
    rate_max_pct   = Column(Float)
    war_pct        = Column(Float)
    ingested_utc   = Column(DateTime)


class CallMoneyRate(Base):
    __tablename__ = "call_money_rates"
    __table_args__ = (UniqueConstraint("trade_date", "product", "maturity_days"),)
    id               = Column(Integer, primary_key=True, autoincrement=True)
    trade_date       = Column(Date, nullable=False)
    product          = Column(String(20))          # Overnight, Short Notice, Term
    maturity_days    = Column(Integer)
    amount_crore     = Column(Float)
    highest_rate_pct = Column(Float)
    lowest_rate_pct  = Column(Float)
    average_rate_pct = Column(Float)
    num_deals        = Column(Integer)
    ingested_utc     = Column(DateTime)


class RefRate(Base):
    """Money market reference rates — DOMMR and BOFR — per product per day."""
    __tablename__ = "ref_rates"
    __table_args__ = (UniqueConstraint("trade_date", "rate_type", "product"),)
    id          = Column(Integer, primary_key=True, autoincrement=True)
    trade_date  = Column(Date, nullable=False)
    rate_type   = Column(String(10), nullable=False)   # DOMMR or BOFR
    product     = Column(String(20), nullable=False)   # Overnight, 1W, 1M, 3M
    amount_crore = Column(Float)
    rate_pct    = Column(Float)
    num_deals   = Column(Integer)
    ingested_utc = Column(DateTime)


class RemittanceMonthly(Base):
    """Wage-earner remittance inflow per month (BB econdata)."""
    __tablename__ = "remittance_monthly"
    __table_args__ = (UniqueConstraint("month"),)
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    month               = Column(String(7), nullable=False)   # "YYYY-MM"
    remittance_usd_mn   = Column(Float)
    remittance_bdt_bn   = Column(Float)
    ingested_utc        = Column(DateTime)


class ReservesMonthly(Base):
    """Foreign-exchange reserves per month (BB econdata), in USD million as published."""
    __tablename__ = "reserves_monthly"
    __table_args__ = (UniqueConstraint("month"),)
    id                        = Column(Integer, primary_key=True, autoincrement=True)
    month                     = Column(String(7), nullable=False)   # "YYYY-MM"
    gross_reserves_usd_mn     = Column(Float)   # Foreign Exchange Reserves (Gross)
    net_reserves_bpm6_usd_mn  = Column(Float)   # Foreign Exchange Reserves (as per BPM6)
    ingested_utc              = Column(DateTime)


class MonetaryMonthly(Base):
    """Monetary & prices indicators per month (BB econdata: inflation, interest
    rates, monetary survey). Fields are OPTIONAL — indicators publish on
    different lags, and only real, sourced values are stored (never fabricated)."""
    __tablename__ = "monetary_monthly"
    __table_args__ = (UniqueConstraint("month"),)
    id                    = Column(Integer, primary_key=True, autoincrement=True)
    month                 = Column(String(7), nullable=False)   # "YYYY-MM"
    cpi_p2p               = Column(Float)   # CPI inflation, point-to-point (YoY)
    cpi_12mo_avg          = Column(Float)   # CPI inflation, 12-month moving average
    m2_growth             = Column(Float)   # broad money (M2) YoY growth
    reserve_money_growth  = Column(Float)   # reserve money YoY growth
    private_credit_growth = Column(Float)   # private-sector credit YoY growth
    wavg_deposit          = Column(Float)   # scheduled-bank weighted-avg deposit rate
    wavg_lending          = Column(Float)   # scheduled-bank weighted-avg lending rate
    source                = Column(String(200))
    ingested_utc          = Column(DateTime)


class PolicyRateSnapshot(Base):
    """BB policy-rate corridor, fetched live from bb.org.bd's POLICY RATES box.
    One row per DISTINCT corridor observed — a new row means a rate change,
    so history + change-detection come for free. last_seen_date advances each
    day the same corridor is re-confirmed."""
    __tablename__ = "policy_rate_snapshots"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    first_seen_date    = Column(Date, nullable=False)
    last_seen_date     = Column(Date)
    repo               = Column(Float)
    slf                = Column(Float)
    sdf                = Column(Float)
    bank_rate          = Column(Float)
    crr                = Column(Float)
    slr                = Column(Float)
    source_last_update = Column(String(40))
    source             = Column(String(200))
    ingested_utc       = Column(DateTime)


class AuctionForecast(Base):
    """One row = one model's forecast of the next auction cutoff for one tenor.

    Written by forecast/run_forecast.py in GitHub Actions — NEVER computed in the
    API (Vercel serverless can't carry the stats stack). The API only reads this.
    `actual_yield` is backfilled by a later run once the auction has printed, so
    every published forecast can be scored against what actually happened.
    """
    __tablename__ = "auction_forecasts"
    __table_args__ = (UniqueConstraint("forecast_run_date", "target_auction_date", "tenor", "model"),)
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    forecast_run_date   = Column(Date, nullable=False)   # when the model ran
    target_auction_date = Column(Date)                   # estimated next auction date
    tenor               = Column(String(15), nullable=False)   # 91D, 182D, 2Y, ...
    instrument          = Column(String(10))             # T_BILL, T_BOND, FRTB
    model               = Column(String(20), nullable=False)   # naive, momentum
    point_yield         = Column(Float)
    lo_yield            = Column(Float)                  # point − 1.96×RMSE
    hi_yield            = Column(Float)                  # point + 1.96×RMSE
    actual_yield        = Column(Float)                  # backfilled after the print
    created_utc         = Column(DateTime)


class BacktestMetric(Base):
    """Rolling out-of-sample skill of one model on one tenor, as of computed_date.

    Errors are in basis points so bills and bonds are directly comparable.
    dir_acc is NULL for the naive model — it predicts zero change, so it makes
    no directional call to score.
    """
    __tablename__ = "backtest_metrics"
    __table_args__ = (UniqueConstraint("computed_date", "model", "tenor"),)
    id            = Column(Integer, primary_key=True, autoincrement=True)
    computed_date = Column(Date, nullable=False)
    model         = Column(String(20), nullable=False)
    tenor         = Column(String(15), nullable=False)
    mae_bps       = Column(Float)
    rmse_bps      = Column(Float)
    # RMSE over the recent slice only — this is what the published band uses,
    # so the interval tracks today's volatility not a calmer average.
    rmse_recent_bps = Column(Float)
    dir_acc       = Column(Float)    # share of correct direction calls, 0–1
    hit_5bps      = Column(Float)    # share of forecasts within ±5 bps, 0–1
    n_obs         = Column(Integer)
    dm_pvalue     = Column(Float)    # Diebold-Mariano vs naive, HLN small-sample corrected
    # Bootstrap 95% CI for this model's own MAE (bps).
    mae_lo        = Column(Float)
    mae_hi        = Column(Float)
    # Bootstrap 95% CI for (naive MAE - this model's MAE) in bps. The decisive
    # number: if this interval contains 0, the edge is not established, however
    # good the point estimate looks.
    gap_lo        = Column(Float)
    gap_hi        = Column(Float)
    coverage      = Column(Float)    # share of actuals inside the published band
    mae_first     = Column(Float)    # first-half MAE (bps)
    mae_recent    = Column(Float)    # second-half MAE (bps) — what today looks like
    verdict       = Column(String(20))  # established | unproven | worse
    # Did this model's DM test survive Benjamini-Hochberg across every
    # comparison in the run? A verdict of "established" requires it.
    dm_fdr_pass   = Column(Boolean)
    band_bps      = Column(Float)   # published band half-width, EWMA-scaled
    # Economic value: bidding this model instead of naive, both shaded to the
    # SAME fill rate so the deployed notional is identical. See
    # forecast/economic_value.py. Only populated for established models.
    ev_pickup_bps      = Column(Float)   # extra yield captured, bps
    ev_shade_bps       = Column(Float)   # shading this model needs for the fill target
    ev_bench_shade_bps = Column(Float)   # shading naive needs for the same fill
    ev_fill_target     = Column(Float)


class BacktestPrediction(Base):
    """Every out-of-sample prediction the backtest made, kept so the tab can
    plot model-vs-actual over history.

    This is NOT the live forecast log — `auction_forecasts` is, and only that
    table proves what was published before an auction. These rows are a
    simulation and are labelled as such wherever they are displayed.
    """
    __tablename__ = "backtest_predictions"
    __table_args__ = (UniqueConstraint("computed_date", "tenor", "model", "auction_date"),)
    id            = Column(Integer, primary_key=True, autoincrement=True)
    computed_date = Column(Date, nullable=False)
    tenor         = Column(String(15), nullable=False)
    model         = Column(String(20), nullable=False)
    auction_date  = Column(Date, nullable=False)
    actual_yield  = Column(Float)
    pred_yield    = Column(Float)


class ResearchDiagnostic(Base):
    """Statistical test results (guide Phase 4) — ADF/KPSS, Granger, Ljung-Box,
    VIF, and OLS coefficient inference with Newey-West errors.

    Written by forecast/run_diagnostics.py, which is the ONLY place statsmodels
    is imported. One row per test per subject so the UI can table them directly.
    """
    __tablename__ = "research_diagnostics"
    __table_args__ = (UniqueConstraint("computed_date", "tenor", "test", "subject"),)
    id            = Column(Integer, primary_key=True, autoincrement=True)
    computed_date = Column(Date, nullable=False)
    tenor         = Column(String(15), nullable=False)
    test          = Column(String(30), nullable=False)   # adf, kpss, granger, ljungbox, vif, ols_coef
    subject       = Column(String(60), nullable=False)   # series or feature name
    statistic     = Column(Float)
    pvalue        = Column(Float)
    lag           = Column(Integer)
    conclusion    = Column(String(120))


class PolicyRateHistory(Base):
    """Effective-dated BB policy corridor — the ANCHOR series for the ECM.

    Distinct from policy_rate_snapshots, which is the live daily fetch and only
    ever knows today's corridor. This table is the step function through time
    that `cutoff - repo` needs in order to be a meaningful spread.

    `verified` is load-bearing, not decoration: an entry is False until it has
    been checked against a BB circular. Modelling REFUSES to use unverified
    history (see forecast/policy.py:usable) — a fabricated anchor would produce
    a confident, wrong error-correction model, which is worse than none.
    """
    __tablename__ = "policy_rate_history"
    __table_args__ = (UniqueConstraint("effective_date"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    effective_date = Column(Date, nullable=False)
    repo           = Column(Float)
    slf            = Column(Float)
    sdf            = Column(Float)
    bank_rate      = Column(Float)
    verified       = Column(Boolean, default=False)
    source         = Column(String(300))
    note           = Column(Text)
    ingested_utc   = Column(DateTime)


class DataIssue(Base):
    """The guardian's memory: one row per distinct problem, not per sighting.

    integrity_check() produces a verdict that is overwritten on every run, so
    nothing recorded that BB's auction calendar had been missing since 27-Sep,
    or how long anything took to resolve. Age is the whole point: "BB is a day
    late" and "BB has stopped publishing" look identical without it.

    `fingerprint` normalises digits out of the message, so the same problem
    seen on successive days is ONE row with a growing age and occurrence count
    rather than a fresh row every run.
    """
    __tablename__ = "data_issues"
    __table_args__ = (UniqueConstraint("fingerprint"),)
    id           = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint  = Column(String(80), nullable=False)
    table_name   = Column(String(40))       # the check group: omo, yields, freshness…
    severity     = Column(String(10))       # defect | waiting
    message      = Column(Text)             # the most recent wording
    first_seen   = Column(DateTime)
    last_seen    = Column(DateTime)
    occurrences  = Column(Integer, default=1)
    resolved_at  = Column(DateTime)         # NULL = still open
    resolution   = Column(String(200))


class DataChange(Base):
    """Append-only trail of what the self-heal actually changed, so every
    automatic correction stays auditable after the fact."""
    __tablename__ = "data_changes"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    changed_utc  = Column(DateTime)
    change_class = Column(String(40))       # yield_issue_date, coupon_payment_date…
    detail       = Column(Text)             # human-readable before -> after


class HolidayCalendar(Base):
    __tablename__ = "holiday_calendar"
    calendar_date  = Column(Date, primary_key=True)
    holiday_name   = Column(String(120))
    holiday_type   = Column(String(30))
    fiscal_year    = Column(String(7))
    source         = Column(String(200))
    # Where a closure came from when it was not on BB's gazetted list: the circular itself.
    evidence_url   = Column(String(400))
    evidence_quote = Column(Text)
    announced_date = Column(Date)


class CalendarNotice(Base):
    """A BB circular about bank closures, and what the system did with it.

    BB announces closures the gazetted list does not carry (Sharadiya Durga Puja 2026: 20-22 Oct,
    by SDAD circular letter 15 of 11-Oct). A closure unknown to the calendar leaves every maturity,
    coupon and settlement on a day the market was shut. A notice is APPLIED to holiday_calendar only
    when the circular's dates are corroborated by something other than the circular (the market was
    silent that day, the gazette overlaps, or it is a government-declared general holiday);
    anything the code cannot be sure of is PENDING and is announced, never guessed.
    """
    __tablename__ = "calendar_notices"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    url            = Column(String(400), nullable=False, unique=True)
    published_date = Column(Date)
    department     = Column(String(40))
    title          = Column(Text)
    kind           = Column(String(12))             # CLOSURE | OTHER
    dates_json     = Column(Text)                   # the dates the circular closes
    evidence_quote = Column(Text)
    status         = Column(String(12))             # APPLIED | PENDING | REJECTED | HISTORICAL | REGIONAL
    reason         = Column(Text)
    first_seen_utc = Column(DateTime)
    decided_utc    = Column(DateTime)
    notified_utc   = Column(DateTime)


class DailyNetFlow(Base):
    __tablename__ = "daily_net_flow"
    flow_date                      = Column(Date, primary_key=True)
    coupon_inflow_bdt_mill         = Column(Float, default=0.0)
    principal_inflow_bdt_mill      = Column(Float, default=0.0)
    total_inflow_bdt_mill          = Column(Float, default=0.0)
    auction_outflow_planned_mill   = Column(Float, default=0.0)
    auction_outflow_confirmed_mill = Column(Float, default=0.0)
    auction_outflow_best_mill      = Column(Float, default=0.0)
    # Early principal repaid through a buyback. Its own line, not folded into
    # principal_inflow: that figure reconciles against maturity_events and a
    # buyback is not a maturity.
    buyback_inflow_bdt_mill        = Column(Float, default=0.0)
    # BGIS sukuk principal and half-yearly profit (BB's outstanding table).
    sukuk_inflow_bdt_mill          = Column(Float, default=0.0)
    net_borrowing_bdt_mill         = Column(Float, default=0.0)
    inflow_security_count          = Column(Integer, default=0)
    coupon_payment_count           = Column(Integer, default=0)
    data_complete                  = Column(Boolean, default=False)
    computed_utc                   = Column(DateTime)


class PipelineRun(Base):
    """One row per data-refresh run — lets the UI show when we last fetched
    (even when a run found 0 new rows), not just when data last changed."""
    __tablename__ = "pipeline_runs"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    run_utc      = Column(DateTime, nullable=False)
    kind         = Column(String(20))      # "refresh" | "reconcile"
    new_rows     = Column(Text)            # JSON: per-step new-row counts
    errors       = Column(Text)            # JSON: list of error strings
    elapsed_sec  = Column(Integer)
    quality      = Column(Text)            # JSON: integrity_check() report


class BgiibAuction(Base):
    """The Islamic window: Bangladesh Government Islami Investment Bond.

    BB auctions BGIIB weekly and publishes the result as a press release.
    Thirteen of them sat in `press_releases` with their figures stored nowhere,
    so the Islamic window appeared in no ladder, no curve and no export — a
    whole instrument missing from a dashboard whose first rule is to never miss
    a product.

    `war_epr_pct` is the weighted-average Expected Profit Rate, the
    Islamic-finance counterpart of a yield and the figure the desk compares
    against a T-Bill cut-off. `psr` is the profit-sharing ratio with the
    government ("90:10") — a split, not a rate, and stored as text so nobody
    can average it by accident.
    """
    __tablename__ = "bgiib_auctions"
    __table_args__ = (UniqueConstraint("auction_date", "tenor_label"),)
    id              = Column(Integer, primary_key=True, autoincrement=True)
    auction_date    = Column(Date, nullable=False)   # the day it was HELD
    tenor_label     = Column(String(8), nullable=False)   # 3M | 6M
    tenor_months    = Column(Integer)
    bids_offered    = Column(Integer)
    offered_crore   = Column(Float)
    bids_accepted   = Column(Integer)
    accepted_crore  = Column(Float)
    psr             = Column(String(12))   # "90:10" — a ratio, never a rate
    war_epr_pct     = Column(Float)        # weighted average Expected Profit Rate
    base_rate_range = Column(String(30))
    epr_range       = Column(String(30))
    source_serial   = Column(String(20))   # BB's own press-release serial
    source          = Column(Text)
    ingested_utc    = Column(DateTime)


class NewsArticle(Base):
    """A news article the Liquidity Watch looked at.

    Every article seen is stored, including the ones filtered out, with the score
    that filtered them: recall is only auditable if the misses are on record.
    `triage_status`: untriaged (waiting for Claude) | skipped (the keyword filter
    said no) | screened_out (Claude said no) | extracted (events found or none) |
    error (the call or the parse failed - retried, never silently dropped).
    """
    __tablename__ = "news_articles"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    url            = Column(Text, nullable=False, unique=True)     # canonical: no query/fragment
    source         = Column(String(40), nullable=False)
    published_utc  = Column(DateTime)
    title          = Column(Text)
    summary        = Column(Text)                                  # the feed's own blurb
    body_text      = Column(Text)                                  # capped; only for candidates
    fetched_utc    = Column(DateTime, nullable=False)
    keyword_score  = Column(Integer)
    triage_status  = Column(String(16), nullable=False, default="untriaged")
    triage_utc     = Column(DateTime)
    triage_model   = Column(String(40))
    triage_json    = Column(Text)                                  # what the model returned, verbatim


class LiquidityEvent(Base):
    """A liquidity event reported in the news that BB's own data does not hold.

    Never summed into a ladder: shown, linked and labelled "from news". `matters`
    is the threshold (>= 500 crore, or a regulatory / BB-credit event of any size);
    `redundant_with` names the table that already holds the event, and such rows
    are hidden by default - the desk does not want the same fact twice.
    """
    __tablename__ = "liquidity_events"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    dedupe_key      = Column(String(200), nullable=False, unique=True)
    kind            = Column(String(32), nullable=False)
    direction       = Column(String(8), nullable=False)             # INJECT | ABSORB | NEUTRAL
    amount_crore    = Column(Float)                                 # NULL when not stated or unverified
    amount_unverified = Column(Boolean, default=False)
    # True when the amount is a RUNNING TOTAL ("Tk 47bn disbursed until mid-August"), not one
    # payment. Shown, never added: a later article says "Tk 70bn until September" and summing
    # both would count the first 47bn twice.
    is_cumulative   = Column(Boolean, default=False)
    event_date      = Column(Date)
    announced_date  = Column(Date)
    status          = Column(String(12))                            # ANNOUNCED | APPROVED | DISBURSED | COMPLETED
    channel         = Column(String(12))                            # CASH | BONDS | BB_CREDIT | REGULATORY
    counterparty    = Column(String(120))
    summary         = Column(Text)
    evidence_quote  = Column(Text, nullable=False)
    sources_json    = Column(Text)                                  # [{url, source, title, published}]
    confidence      = Column(Float)
    matters         = Column(Boolean, default=False)
    redundant_with  = Column(String(60))
    first_seen_utc  = Column(DateTime)
    updated_utc     = Column(DateTime)
    notified_utc    = Column(DateTime)
    verified_by     = Column(String(12), default="llm")             # llm | research | human


class LiquidityEventHistory(Base):
    """How an event changed: ANNOUNCED -> DISBURSED, or a corrected amount."""
    __tablename__ = "liquidity_event_history"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    event_id    = Column(Integer, nullable=False)
    changed_utc = Column(DateTime, nullable=False)
    field       = Column(String(24), nullable=False)
    old_value   = Column(Text)
    new_value   = Column(Text)
    source_url  = Column(Text)


class DeskBuybackMark(Base):
    """A buyback BB has announced but not yet published results for, as the desk knows it.

    BB's first buyback settled on 12-Oct-2026 and its figures reached the system
    days later; the desk knew the accepted face value the day of the auction. Like
    a cut-off mark this is PROVISIONAL and never BB data: it lives in its own
    table, is shown labelled everywhere, and is NEVER added to a ladder total -
    cash that moves a number the desk then trades on must come from BB's own
    event, not a hand-typed one. Once buyback_events holds the same ISIN and
    settlement date, BB's figure is the one that counts and this row is only the
    record of what the desk believed.
    """
    __tablename__ = "desk_buyback_marks"
    __table_args__ = (UniqueConstraint("isin", "settlement_date"),)
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    isin                = Column(String(12), nullable=False)
    settlement_date     = Column(Date, nullable=False)
    accepted_face_crore = Column(Float, nullable=False)
    cash_paid_crore     = Column(Float)             # NULL until known - never derived
    price_per_100       = Column(Float)
    entered_by          = Column(String(60))
    note                = Column(Text)
    entered_utc         = Column(DateTime)
    updated_utc         = Column(DateTime)


class SukukOutstanding(Base):
    """BD Govt Investment Sukuk (BGIS) outstanding, from BB's own table.

    A product the dashboard held none of: eleven issues, 53,500 crore, and a
    sukuk maturing is government cash into the market just as a bond is. One row
    per ISIN, refreshed each fetch. `profit_frequency` is BB's wording ("Half
    Yearly" / "on Maturity") and decides whether profit payments can be derived:
    only an explicit frequency is ever turned into a schedule.
    """
    __tablename__ = "sukuk_outstanding"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    isin              = Column(String(14), nullable=False, unique=True)
    project           = Column(Text)
    tenor_label       = Column(String(12))          # BB's text: "10 Y", "273 D"
    contract          = Column(String(60))          # Ijarah / Istisna'a ...
    issue_date        = Column(Date, nullable=False)
    maturity_date     = Column(Date, nullable=False)
    profit_rate_pct   = Column(Float)
    profit_frequency  = Column(String(30))
    tradability       = Column(String(60))
    outstanding_crore = Column(Float, nullable=False)
    first_seen_utc    = Column(DateTime)
    last_seen_utc     = Column(DateTime)
    source            = Column(Text)


class SukukDocument(Base):
    """A BGIS auction notice or prospectus BB has posted.

    The point is to tell the desk the day a new one appears: a notice is the
    announcement of an issue that will pull cash out of the market. `notified_utc`
    is set only after the announcement issue exists (see engines/announce.py).
    """
    __tablename__ = "sukuk_documents"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    doc_key        = Column(String(80), nullable=False, unique=True)   # sukuk-<pdf stem>
    url            = Column(Text, nullable=False)
    kind           = Column(String(12), nullable=False)                # NOTICE | PROSPECTUS
    doc_date       = Column(Date)
    ref            = Column(String(40))                                # BB's reference no.
    title          = Column(Text)
    first_seen_utc = Column(DateTime)
    notified_utc   = Column(DateTime)


class DeskMark(Base):
    """What the desk saw at the auction, on the day — never what BB published.

    BB's results page prints the ISSUE date and the auction was the previous
    working day, so a cut-off reaches this system a day or two after the desk
    already traded off it. The desk is in the auction and knows the number that
    afternoon; this is where it says so.

    It is a SEPARATE table on purpose. A desk mark must never be written into
    primary_yield_snapshots: that table is BB's own statement of record, and
    one unverified figure sitting in it indistinguishable from the rest would
    undo the thing this project exists for. A mark is provisional, labelled as
    such everywhere it appears, and BB's published cut-off supersedes it
    automatically — not by overwriting the mark, which stays as the audit
    trail of what the desk believed and when, but by being preferred wherever
    both exist.

    The divergence between the two is itself worth reading: a mark 40bp from
    BB's print is either a typo or a surprise in the auction, and both are
    things the desk wants to know it got wrong.
    """
    __tablename__ = "desk_marks"
    __table_args__ = (UniqueConstraint("auction_date", "tenor_label"),)
    id                = Column(Integer, primary_key=True, autoincrement=True)
    auction_date      = Column(Date, nullable=False)   # the day bids were taken
    tenor_label       = Column(String(15), nullable=False)
    security_type     = Column(String(10))
    cutoff_yield_pct  = Column(Float, nullable=False)
    accepted_crore    = Column(Float)     # optional; blank when not yet known
    entered_by        = Column(String(60))
    entered_utc       = Column(DateTime)
    updated_utc       = Column(DateTime)
    note              = Column(Text)


class PressRelease(Base):
    """BB's press-release listing, classified and kept.

    One page carries nearly everything BB announces: OMO operations, auction
    results, auction notices, BGIIB (Islamic bond) results and — since
    08-Oct-2026 — bond buybacks. Three failures of this project were all
    sitting in it: the OMO links that moved and went unnoticed for eleven
    days, the first buyback whose 1,716.14 crore never reached the ladder, and
    the weekly BGIIB auctions this project still does not capture.

    Storing the listing is what makes "BB announced something we have no
    record of" answerable. A release in a category we handle but with no
    matching event is a defect; an unrecognised release keeps its title
    verbatim and is categorised OTHER, because a category invented for a
    release nobody read is worse than admitting it is unread.
    """
    __tablename__ = "press_releases"
    __table_args__ = (UniqueConstraint("serial"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    serial         = Column(String(20), nullable=False)   # pr14322
    published_date = Column(Date)
    # The date the release is ABOUT, which is not when it was posted: "Open
    # Market Operations as on 13 October 2026" went up on the 14th. Storing
    # only the publication date is how a one-day lag becomes a permanent
    # off-by-one in everything derived from it.
    subject_date   = Column(Date)
    title          = Column(Text)
    url            = Column(Text)
    category       = Column(String(24))
    isin           = Column(String(12))
    first_seen_utc = Column(DateTime)
    last_seen_utc  = Column(DateTime)
    # When the desk was TOLD about this release (a GitHub issue, which emails
    # the repo owner). NULL = not yet announced. Set only after the issue was
    # actually created, so a failed notification is retried, never lost.
    notified_utc   = Column(DateTime)


class BuybackEvent(Base):
    """A government buyback of its own bonds — an EARLY principal repayment.

    Bangladesh's first ever was announced on 08-Oct-2026 under the Liability
    Management Operation: ISIN BD0926191026, the 2-year bond issued 06-Nov-2024,
    56 bids for 2,016.14 crore of which 1,716.14 crore was accepted. BB's press
    release states neither the settlement date nor the price, so both were
    established from GSOM's daily outstanding, which fell from 66,665.80 to
    49,504.40 million between 11 and 12 October — 17,161.40 million, exactly
    the accepted amount. That drop is what proves the figure is FACE value and
    that it settled on 12-Oct.

    It is not a maturity (maturity_events allows one row per ISIN, and the bond
    still matures on 06-Nov with its remaining 49,504.40 million) and it is not
    an auction. It is cash BB paid into the market on a day nothing else
    explained, so it belongs in the ladder on its own line.

    `cash_paid_crore` stays NULL when BB does not publish the price. The bond
    was quoted at a 7.96% yield against a 12.3% coupon, so it traded at a clear
    premium and the cash paid exceeded par — but by how much is not published,
    and the ladder falls back to face with that stated rather than inventing a
    price. Same precedence as an auction with no published sale value.
    """
    __tablename__ = "buyback_events"
    __table_args__ = (UniqueConstraint("isin", "settlement_date"),)
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    isin                = Column(String(12), nullable=False)
    announced_date      = Column(Date)
    auction_date        = Column(Date)
    settlement_date     = Column(Date, nullable=False)
    bids_received_count = Column(Integer)
    offered_face_crore  = Column(Float)      # bids submitted
    accepted_face_crore = Column(Float)      # bids accepted — par retired
    cash_paid_crore     = Column(Float)      # NULL unless BB publishes it
    source              = Column(Text)       # the press release
    verified_by         = Column(Text)       # how the figure was corroborated
    notes               = Column(Text)
    ingested_utc        = Column(DateTime)


class DataAbsence(Base):
    """A date Bangladesh Bank does not publish, confirmed by looking.

    The gap check treats a missing working day inside a series as OUR defect,
    which is right in general and wrong for the days BB simply skips. On
    Thursday 08-Oct-2026 the interbank FX turnover table carried no row while
    call money, repo, OMO and reference rates all printed normally; BB's own
    two-month view, re-read on 15-Oct, still has no 08-Oct. Nothing we can do
    will ever fill it, so leaving it as a defect means one permanent red line
    — and a permanently red check is one nobody reads.

    An absence is only ever recorded from EVIDENCE: a successful fetch whose
    window straddles the date and came back without it. It is never a mute
    button, which is why the date must sit strictly inside the fetched range —
    a failed fetch records nothing and stays a defect.
    """
    __tablename__ = "data_absences"
    __table_args__ = (UniqueConstraint("dataset", "absent_date"),)
    id           = Column(Integer, primary_key=True, autoincrement=True)
    dataset      = Column(String(40), nullable=False)   # expectations key
    absent_date  = Column(Date, nullable=False)
    reason       = Column(Text)
    source       = Column(Text)        # the page that was re-read
    verified_utc = Column(DateTime)
    checks       = Column(Integer, default=1)   # times re-confirmed
    # WHOSE hole it is. 'bb_omission': BB's own page, read from the start of the
    # month, carries no row. 'lost': we HAD it and destroyed it, and BB cannot
    # give it back — kept apart because the digest must never tell the desk a
    # hole is BB's when it is ours.
    kind         = Column(String(16), default="bb_omission")


class SourceSnapshot(Base):
    """What BB's pages LOOKED LIKE, fetch by fetch.

    Every other table here records what BB said. None recorded the shape of
    the page it was said on, so when BB renamed its press-release links from
    `a.pdf-file` to `a.pdf-list` the fetcher matched nothing, returned an empty
    list, and that was indistinguishable from a quiet week — for eleven days.

    One row per distinct SHAPE, not per fetch: selector, column count,
    normalised header texts, and how many rows were seen, parsed and skipped.
    `seen_count` is the evidence for which shape is the real one; a shape seen
    four hundred times is known-good, and a brand-new shape that parses fewer
    rows is a change worth waking up for. The verdict is computed against the
    longest-held shape, never against last time, because comparing to last
    time lets a broken shape quietly become the new normal on its second run.
    """
    __tablename__ = "source_snapshots"
    __table_args__ = (UniqueConstraint("source", "structure_hash"),)
    id             = Column(Integer, primary_key=True, autoincrement=True)
    source         = Column(String(40), nullable=False)   # treasury, omo, calendar…
    structure_hash = Column(String(40), nullable=False)   # selector + headers
    url            = Column(Text)
    selector       = Column(String(80))     # which selector actually matched
    column_count   = Column(Integer)
    headers        = Column(Text)           # JSON: normalised header texts
    rows_seen      = Column(Integer)
    rows_parsed    = Column(Integer)
    rows_skipped   = Column(Integer)
    skip_reasons   = Column(Text)           # JSON: {reason: count}
    unmapped       = Column(Text)           # JSON: columns nobody claims
    mapped         = Column(Text)           # JSON: {field: column index}
    verdict        = Column(String(12))     # ok | changed | degraded
    reasons        = Column(Text)           # JSON: list of strings
    first_seen     = Column(DateTime)
    last_seen      = Column(DateTime)
    seen_count     = Column(Integer, default=1)


# ── Engine & Session ──────────────────────────────────────────────────────────

def get_engine():
    if DB_PATH:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_sqlite = DB_URL.startswith("sqlite")
    kwargs = dict(echo=False, pool_pre_ping=True)
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(DB_URL, **kwargs)

def get_session():
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()

def fix_all_sequences():
    """Re-sync every autoincrement `id` sequence to MAX(id). PostgreSQL only.

    Bulk loads / restores / manual inserts can leave a table's id sequence
    BEHIND MAX(id); the next autoincrement insert then collides on the primary
    key ('duplicate key (id)=N') and the whole fetch step fails — silently
    blocking updates (this is what stalled OMO). Running this before every
    refresh makes writes bulletproof: a stale sequence can never block a fetch.
    """
    engine = get_engine()
    if not str(engine.url).startswith("postgresql"):
        return
    id_tables = [t.name for t in Base.metadata.sorted_tables
                 if t.c.get("id") is not None and t.c["id"].primary_key]
    for tname in id_tables:
        try:
            with engine.connect() as conn:
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{tname}','id'), "
                    f"GREATEST((SELECT COALESCE(MAX(id),1) FROM {tname}),1))"
                ))
                conn.commit()
        except Exception as exc:
            log.warning("sequence fix skipped for %s: %s", tname, exc)
    log.info("id sequences re-synced (%d tables)", len(id_tables))


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    # Lightweight migrations for existing DBs. Each runs in its OWN connection so a
    # failed/no-op ALTER never aborts the transaction for the next one (Postgres
    # aborts the whole tx on the first error otherwise).
    migrations = [
        "ALTER TABLE omo_transactions ADD COLUMN IF NOT EXISTS maturity_bdt_crore REAL",
        "ALTER TABLE omo_transactions ADD COLUMN IF NOT EXISTS rate_range VARCHAR(30)",
        "ALTER TABLE omo_transactions ADD COLUMN IF NOT EXISTS source_pub_date DATE",
        "ALTER TABLE omo_transactions ADD COLUMN IF NOT EXISTS source_serial VARCHAR(40)",
        "ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS quality TEXT",
        "ALTER TABLE press_releases ADD COLUMN IF NOT EXISTS notified_utc TIMESTAMP",
        # SQLite has no IF NOT EXISTS for columns, and the local file is a live
        # database. Without this a stale local copy fails every press query with
        # "no such column"; on Postgres this errors harmlessly (column exists).
        "ALTER TABLE press_releases ADD COLUMN notified_utc TIMESTAMP",
        "ALTER TABLE liquidity_events ADD COLUMN IF NOT EXISTS is_cumulative BOOLEAN DEFAULT FALSE",
        "ALTER TABLE liquidity_events ADD COLUMN is_cumulative BOOLEAN DEFAULT 0",
        "ALTER TABLE data_absences ADD COLUMN IF NOT EXISTS kind VARCHAR(16) DEFAULT 'bb_omission'",
        "ALTER TABLE daily_net_flow ADD COLUMN IF NOT EXISTS buyback_inflow_bdt_mill REAL DEFAULT 0",
        "ALTER TABLE daily_net_flow ADD COLUMN IF NOT EXISTS sukuk_inflow_bdt_mill REAL DEFAULT 0",
        "ALTER TABLE holiday_calendar ADD COLUMN IF NOT EXISTS evidence_url VARCHAR(400)",
        "ALTER TABLE holiday_calendar ADD COLUMN IF NOT EXISTS evidence_quote TEXT",
        "ALTER TABLE holiday_calendar ADD COLUMN IF NOT EXISTS announced_date DATE",
        # SQLite has no IF NOT EXISTS for columns; errors harmlessly on Postgres.
        "ALTER TABLE daily_net_flow ADD COLUMN sukuk_inflow_bdt_mill REAL DEFAULT 0",
        "ALTER TABLE holiday_calendar ADD COLUMN evidence_url VARCHAR(400)",
        "ALTER TABLE holiday_calendar ADD COLUMN evidence_quote TEXT",
        "ALTER TABLE holiday_calendar ADD COLUMN announced_date DATE",
        # A desk mark is a provisional cut-off, never BB's. A sovereign yield
        # outside 0-30% is a typo in any century, and the constraint is the
        # layer below every code path that could write one.
        "ALTER TABLE desk_marks ADD CONSTRAINT ck_desk_cutoff_band "
        "CHECK (cutoff_yield_pct > 0 AND cutoff_yield_pct <= 30) NOT VALID",
        "ALTER TABLE desk_marks VALIDATE CONSTRAINT ck_desk_cutoff_band",
        # ONE AUCTION, ONE ROW. This table is keyed (tenor_label,
        # auction_date), which cannot stop the same auction being stored twice
        # under two different labels: on 10-Oct-2026 a 5-year bond re-issued
        # with 2.60 years left landed as both "2Y" (correct — BB labels a
        # re-issue by its remaining maturity) and "5Y", same ISIN, same
        # cut-off, same 2,500 crore, and double-booked 2,425.59 crore of cash
        # outflow on 05-Feb. BB never auctions one ISIN twice on one day, so
        # the pair is the invariant. Partial, because rows from before the
        # parser kept the ISIN have NULL there and NULLs never conflict.
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_pys_auction_isin "
        "ON primary_yield_snapshots (auction_date, isin) WHERE isin IS NOT NULL",
        # The cash paid, beside the face value allotted. See AuctionEvent.
        "ALTER TABLE auction_events ADD COLUMN IF NOT EXISTS sale_value_bdt_crore REAL",
        "ALTER TABLE auction_events ADD COLUMN IF NOT EXISTS sale_value_bdt_mill REAL",
        # BB's results page has always carried these; the parser now keeps them.
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS isin VARCHAR(12)",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS is_reissue BOOLEAN DEFAULT FALSE",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS reissue_remaining VARCHAR(20)",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS original_tenor_label VARCHAR(15)",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS original_tenor_years REAL",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS sale_value_bdt_crore REAL",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS bids_received_count INTEGER",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS bids_accepted_count INTEGER",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS yield_range_received VARCHAR(40)",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS yield_range_accepted VARCHAR(40)",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS weighted_avg_price REAL",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS devolvement_yield_pct REAL",
        "ALTER TABLE primary_yield_snapshots ADD COLUMN IF NOT EXISTS issue_date DATE",
        # ── Invariants: the layer BELOW every code path, so a writer that skips
        # the canonical upsert (reconcile.py did, for months) cannot land a broken
        # row. Postgres-only syntax; on SQLite each one no-ops and the loop moves
        # on. NOT VALID then VALIDATE, so adding them proves the existing rows.
        "ALTER TABLE primary_yield_snapshots ADD CONSTRAINT ck_pys_issue_date_present CHECK (issue_date IS NOT NULL) NOT VALID",
        "ALTER TABLE primary_yield_snapshots VALIDATE CONSTRAINT ck_pys_issue_date_present",
        # >= not >: two 2007 rows are legacy cases where BB published a single
        # date, so auction == issue. An INVERTED pair is always a parse error.
        "ALTER TABLE primary_yield_snapshots ADD CONSTRAINT ck_pys_issue_after_auction CHECK (issue_date >= auction_date) NOT VALID",
        "ALTER TABLE primary_yield_snapshots VALIDATE CONSTRAINT ck_pys_issue_after_auction",
        "ALTER TABLE omo_transactions ADD CONSTRAINT ck_omo_maturity_after_txn CHECK (maturity_date >= transaction_date) NOT VALID",
        "ALTER TABLE omo_transactions VALIDATE CONSTRAINT ck_omo_maturity_after_txn",
        "ALTER TABLE call_money_rates ADD CONSTRAINT ck_cm_rate_band CHECK (lowest_rate_pct <= average_rate_pct AND average_rate_pct <= highest_rate_pct) NOT VALID",
        "ALTER TABLE interbank_repo ADD CONSTRAINT ck_repo_rate_band CHECK (rate_min_pct <= war_pct AND war_pct <= rate_max_pct) NOT VALID",
        "ALTER TABLE interbank_repo VALIDATE CONSTRAINT ck_repo_rate_band",
        "ALTER TABLE fx_rates_daily ADD CONSTRAINT ck_fx_bid_le_ask CHECK (bid_rate <= ask_rate) NOT VALID",
        "ALTER TABLE fx_rates_daily VALIDATE CONSTRAINT ck_fx_bid_le_ask",
        "ALTER TABLE interbank_fx ADD CONSTRAINT ck_ibfx_rate_band CHECK (low_rate <= war_rate AND war_rate <= high_rate) NOT VALID",
        "ALTER TABLE interbank_fx VALIDATE CONSTRAINT ck_ibfx_rate_band",
        "ALTER TABLE call_money_rates VALIDATE CONSTRAINT ck_cm_rate_band",
        # Forecast tables: the unique keys are what keep a re-run idempotent —
        # run_forecast.py looks up on exactly these columns before writing, and
        # the index stops a concurrent/partial run from double-inserting. If a
        # table pre-dates the constraint, create_all() will NOT add it, so the
        # index is created explicitly here.
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_auction_forecasts_key "
        "ON auction_forecasts (forecast_run_date, target_auction_date, tenor, model)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_backtest_metrics_key "
        "ON backtest_metrics (computed_date, model, tenor)",
        "ALTER TABLE auction_forecasts ADD COLUMN IF NOT EXISTS actual_yield DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS dm_pvalue DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS mae_lo DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS mae_hi DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS gap_lo DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS gap_hi DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS coverage DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS mae_first DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS mae_recent DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS verdict VARCHAR(20)",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS rmse_recent_bps DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS dm_fdr_pass BOOLEAN",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS band_bps DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS ev_pickup_bps DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS ev_shade_bps DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS ev_bench_shade_bps DOUBLE PRECISION",
        "ALTER TABLE backtest_metrics ADD COLUMN IF NOT EXISTS ev_fill_target DOUBLE PRECISION",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_backtest_predictions_key "
        "ON backtest_predictions (computed_date, tenor, model, auction_date)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_research_diagnostics_key "
        "ON research_diagnostics (computed_date, tenor, test, subject)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_policy_rate_history_key "
        "ON policy_rate_history (effective_date)",
    ]
    for ddl in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(ddl))
                conn.commit()
        except Exception:
            pass  # column already exists / SQLite (no IF NOT EXISTS) — harmless
    log.info("Database initialised at %s", DB_PATH)
