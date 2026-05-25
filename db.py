"""
db.py — SQLite schema creation and session factory.
All tables derived from the canonical schema in the blueprint.
"""
import logging
import os
from pathlib import Path
from sqlalchemy import (
    create_engine, Column, String, Date, DateTime, Integer,
    Float, Boolean, Text, UniqueConstraint, event
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
    rate_pct            = Column(Float)
    direction           = Column(String(20))        # INJECTION or ABSORPTION
    source_pdf          = Column(String(300))
    ingested_utc        = Column(DateTime)


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

def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    # Add maturity_bdt_crore column if it doesn't exist yet (migration for existing DBs)
    with engine.connect() as conn:
        try:
            conn.execute(__import__("sqlalchemy").text(
                "ALTER TABLE omo_transactions ADD COLUMN maturity_bdt_crore REAL"
            ))
            conn.commit()
            log.info("Migrated omo_transactions: added maturity_bdt_crore column")
        except Exception:
            pass  # column already exists
    log.info("Database initialised at %s", DB_PATH)
