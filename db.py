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
    auction_date       = Column(Date)
    security_type      = Column(String(10))
    tenor_label        = Column(String(15))
    tenor_years        = Column(Float)
    cutoff_yield_pct   = Column(Float)
    offered_bdt_crore  = Column(Float)
    accepted_bdt_crore = Column(Float)
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


class HolidayCalendar(Base):
    __tablename__ = "holiday_calendar"
    calendar_date  = Column(Date, primary_key=True)
    holiday_name   = Column(String(120))
    holiday_type   = Column(String(30))
    fiscal_year    = Column(String(7))
    source         = Column(String(200))


class DailyNetFlow(Base):
    __tablename__ = "daily_net_flow"
    flow_date                      = Column(Date, primary_key=True)
    coupon_inflow_bdt_mill         = Column(Float, default=0.0)
    principal_inflow_bdt_mill      = Column(Float, default=0.0)
    total_inflow_bdt_mill          = Column(Float, default=0.0)
    auction_outflow_planned_mill   = Column(Float, default=0.0)
    auction_outflow_confirmed_mill = Column(Float, default=0.0)
    auction_outflow_best_mill      = Column(Float, default=0.0)
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
