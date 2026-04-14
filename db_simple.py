"""db_simple.py — Lightweight sqlite3 replacement for environments without SQLAlchemy."""
import sqlite3, datetime, pathlib
from config import DB_PATH

def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS holiday_calendar (
        calendar_date TEXT PRIMARY KEY,
        holiday_name  TEXT,
        holiday_type  TEXT,
        fiscal_year   TEXT,
        source        TEXT
    );
    CREATE TABLE IF NOT EXISTS securities (
        isin TEXT PRIMARY KEY, security_name_raw TEXT, security_name_norm TEXT,
        security_type TEXT, issue_date TEXT, maturity_date TEXT,
        coupon_rate_pct REAL, coupon_frequency TEXT, issue_price REAL,
        outstanding_bdt_mill REAL, source_page TEXT,
        source_settlement_date TEXT, data_quality TEXT, last_updated_utc TEXT
    );
    CREATE TABLE IF NOT EXISTS maturity_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, isin TEXT UNIQUE,
        scheduled_date TEXT, payment_date TEXT, principal_bdt_mill REAL,
        outstanding_snapshot_date TEXT, roll_days INTEGER, roll_reason TEXT,
        calc_method TEXT, formula_string TEXT, data_quality TEXT
    );
    CREATE TABLE IF NOT EXISTS coupon_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, isin TEXT, scheduled_date TEXT,
        payment_date TEXT, amount_bdt_mill REAL, coupon_rate_used_pct REAL,
        outstanding_used_bdt_mill REAL, outstanding_snapshot_date TEXT,
        period_days_actual INTEGER, calc_method TEXT, formula_string TEXT,
        is_derived INTEGER, is_short_coupon INTEGER, data_quality TEXT,
        UNIQUE(isin, scheduled_date)
    );
    CREATE TABLE IF NOT EXISTS auction_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fiscal_year TEXT, auction_no INTEGER,
        auction_date TEXT, settlement_date TEXT, security_type TEXT,
        tenor_label TEXT, offered_amount_bdt_crore REAL, offered_amount_bdt_mill REAL,
        accepted_amount_bdt_crore REAL, accepted_amount_bdt_mill REAL,
        weighted_avg_yield_pct REAL, resulting_isin TEXT, outflow_status TEXT,
        roll_days INTEGER, roll_reason TEXT, source TEXT, data_quality TEXT,
        UNIQUE(fiscal_year, auction_no, tenor_label)
    );
    CREATE TABLE IF NOT EXISTS daily_net_flow (
        flow_date TEXT PRIMARY KEY, coupon_inflow_bdt_mill REAL,
        principal_inflow_bdt_mill REAL, total_inflow_bdt_mill REAL,
        auction_outflow_planned_mill REAL, auction_outflow_confirmed_mill REAL,
        auction_outflow_best_mill REAL, net_borrowing_bdt_mill REAL,
        inflow_security_count INTEGER, coupon_payment_count INTEGER,
        data_complete INTEGER, computed_utc TEXT
    );
    """)
    conn.commit()
    conn.close()
