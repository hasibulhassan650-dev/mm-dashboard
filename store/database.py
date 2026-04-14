"""
store/database.py

SQLite persistence for securities, snapshots, and all event tables.
Uses pandas read_sql / to_sql for simplicity.
No ORM — direct sqlite3 for schema creation.
"""

import logging
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from config import DB_PATH

logger = logging.getLogger(__name__)

# ── Schema DDL ─────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS securities (
    isin                     TEXT NOT NULL,
    security_name_raw        TEXT,
    security_name_normalized TEXT,
    security_type            TEXT,
    issue_date               TEXT,
    issue_date_raw           TEXT,
    maturity_date            TEXT,
    maturity_date_raw        TEXT,
    coupon_rate_pct          REAL,
    coupon_frequency         TEXT,
    last_coupon_date         TEXT,
    last_coupon_date_raw     TEXT,
    next_coupon_date         TEXT,
    next_coupon_date_raw     TEXT,
    issue_price              REAL,
    outstanding_bdt_mill     REAL,
    outstanding_raw          TEXT,
    market_yield_pct         REAL,
    market_price             REAL,
    remaining_maturity_raw   TEXT,
    remaining_maturity_value REAL,
    remaining_maturity_unit  TEXT,
    settlement_date          TEXT,
    yield_date               TEXT,
    source_page              TEXT,
    source_row_index         INTEGER,
    data_quality             TEXT,
    last_updated             TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (isin, settlement_date)
);

CREATE TABLE IF NOT EXISTS coupon_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    isin                        TEXT NOT NULL,
    scheduled_date              TEXT NOT NULL,
    payment_date                TEXT NOT NULL,
    days_rolled                 INTEGER,
    roll_reason                 TEXT,
    was_rolled                  INTEGER,
    coupon_amount_bdt_mill      REAL,
    outstanding_used_bdt_mill   REAL,
    outstanding_snapshot_date   TEXT,
    coupon_rate_used_pct        REAL,
    coupon_frequency            TEXT,
    period_days_actual          INTEGER,
    calc_method                 TEXT,
    formula_string              TEXT,
    is_derived                  INTEGER,
    is_short_coupon             INTEGER,
    event_type                  TEXT DEFAULT 'COUPON',
    source_page                 TEXT,
    data_quality                TEXT,
    last_updated                TEXT DEFAULT (datetime('now')),
    UNIQUE (isin, scheduled_date)
);

CREATE TABLE IF NOT EXISTS maturity_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    isin                        TEXT NOT NULL UNIQUE,
    security_name               TEXT,
    security_type               TEXT,
    scheduled_date              TEXT NOT NULL,
    payment_date                TEXT NOT NULL,
    days_rolled                 INTEGER,
    roll_reason                 TEXT,
    was_rolled                  INTEGER,
    principal_bdt_mill          REAL,
    outstanding_used_bdt_mill   REAL,
    outstanding_snapshot_date   TEXT,
    calc_method                 TEXT,
    formula_string              TEXT,
    event_type                  TEXT DEFAULT 'PRINCIPAL_MATURITY',
    source_page                 TEXT,
    source_row_index            INTEGER,
    data_quality                TEXT,
    last_updated                TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS auction_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_no                  TEXT,
    auction_no_int              INTEGER,
    fiscal_year                 TEXT,
    auction_date                TEXT NOT NULL,
    settlement_date             TEXT,
    days_rolled                 INTEGER,
    roll_reason                 TEXT,
    security_type               TEXT,
    tenor_label                 TEXT,
    offered_amount_bdt_crore    REAL,
    offered_amount_bdt_mill     REAL,
    accepted_amount_bdt_crore   REAL,
    accepted_amount_bdt_mill    REAL,
    best_amount_bdt_mill        REAL,
    weighted_avg_yield_pct      REAL,
    outflow_status              TEXT DEFAULT 'PLANNED',
    resulting_isin              TEXT,
    event_type                  TEXT DEFAULT 'AUCTION',
    source                      TEXT,
    data_quality                TEXT,
    last_updated                TEXT DEFAULT (datetime('now')),
    UNIQUE (auction_date, tenor_label, security_type)
);

CREATE TABLE IF NOT EXISTS daily_net_flow (
    flow_date                   TEXT PRIMARY KEY,
    coupon_inflow_bdt_mill      REAL,
    principal_inflow_bdt_mill   REAL,
    total_inflow_bdt_mill       REAL,
    auction_outflow_planned     REAL,
    auction_outflow_confirmed   REAL,
    auction_outflow_best        REAL,
    net_borrowing_bdt_mill      REAL,
    tbill_maturity_bdt_mill     REAL,
    tbond_maturity_bdt_mill     REAL,
    frtb_maturity_bdt_mill      REAL,
    tbill_outflow_bdt_mill      REAL,
    bond_frtb_outflow_bdt_mill  REAL,
    inflow_security_count       INTEGER,
    coupon_count                INTEGER,
    auction_tenor_count         INTEGER,
    data_complete               INTEGER,
    cumulative_net_borrowing    REAL,
    last_updated                TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url    TEXT,
    run_at        TEXT DEFAULT (datetime('now')),
    status        TEXT,
    rows_parsed   INTEGER,
    error_message TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db() -> None:
    """Create all tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
    logger.info("Database initialized at %s", DB_PATH)


def upsert_securities(records: list[dict]) -> int:
    """
    Insert or replace securities. UNIQUE key is (isin, settlement_date).
    Returns count of rows written.
    """
    if not records:
        return 0
    df = _records_to_df(records)
    _date_cols_to_str(df, ["issue_date", "maturity_date", "last_coupon_date",
                            "next_coupon_date", "settlement_date", "yield_date"])
    with get_connection() as conn:
        df.to_sql("securities", conn, if_exists="append", index=False,
                  method=_upsert_method("securities", "isin, settlement_date"))
    logger.info("Upserted %d securities", len(df))
    return len(df)


def upsert_coupon_events(records: list[dict]) -> int:
    if not records:
        return 0
    df = _records_to_df(records)
    _date_cols_to_str(df, ["scheduled_date", "payment_date", "outstanding_snapshot_date"])
    _bool_cols_to_int(df, ["was_rolled", "is_derived", "is_short_coupon"])
    with get_connection() as conn:
        df.to_sql("coupon_events", conn, if_exists="append", index=False,
                  method=_upsert_method("coupon_events", "isin, scheduled_date"))
    logger.info("Upserted %d coupon events", len(df))
    return len(df)


def upsert_maturity_events(records: list[dict]) -> int:
    if not records:
        return 0
    df = _records_to_df(records)
    _date_cols_to_str(df, ["scheduled_date", "payment_date", "outstanding_snapshot_date"])
    _bool_cols_to_int(df, ["was_rolled"])
    with get_connection() as conn:
        df.to_sql("maturity_events", conn, if_exists="append", index=False,
                  method=_upsert_method("maturity_events", "isin"))
    logger.info("Upserted %d maturity events", len(df))
    return len(df)


def upsert_auction_events(records: list[dict]) -> int:
    if not records:
        return 0
    df = _records_to_df(records)
    _date_cols_to_str(df, ["auction_date", "settlement_date"])
    with get_connection() as conn:
        df.to_sql("auction_events", conn, if_exists="append", index=False,
                  method=_upsert_method(
                      "auction_events", "auction_date, tenor_label, security_type"
                  ))
    logger.info("Upserted %d auction events", len(df))
    return len(df)


def upsert_daily_flow(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df = df.copy()
    _date_cols_to_str(df, ["flow_date"])
    _bool_cols_to_int(df, ["data_complete"])
    with get_connection() as conn:
        df.to_sql("daily_net_flow", conn, if_exists="replace", index=False)
    logger.info("Written %d daily flow rows", len(df))
    return len(df)


def log_scrape(source_url: str, status: str, rows_parsed: int, error: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO scrape_log (source_url, status, rows_parsed, error_message) "
            "VALUES (?, ?, ?, ?)",
            (source_url, status, rows_parsed, error),
        )


# ── Query helpers ──────────────────────────────────────────────────────────────

def query_daily_flow(date_from: date, date_to: date) -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT * FROM daily_net_flow "
            "WHERE flow_date BETWEEN ? AND ? ORDER BY flow_date",
            conn,
            params=(date_from.isoformat(), date_to.isoformat()),
        )


def query_events_on_date(d: date) -> dict:
    """Return all events for a single date, grouped by type."""
    with get_connection() as conn:
        coupons    = pd.read_sql(
            "SELECT * FROM coupon_events WHERE payment_date=? ORDER BY isin",
            conn, params=(d.isoformat(),)
        )
        maturities = pd.read_sql(
            "SELECT * FROM maturity_events WHERE payment_date=? ORDER BY isin",
            conn, params=(d.isoformat(),)
        )
        auctions   = pd.read_sql(
            "SELECT * FROM auction_events WHERE settlement_date=? ORDER BY tenor_label",
            conn, params=(d.isoformat(),)
        )
    return {"coupons": coupons, "maturities": maturities, "auctions": auctions}


def query_securities() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT * FROM securities s1 "
            "WHERE settlement_date = ("
            "  SELECT MAX(settlement_date) FROM securities s2 WHERE s2.isin=s1.isin"
            ") ORDER BY security_type, maturity_date",
            conn,
        )


def query_all_events(date_from: date, date_to: date) -> pd.DataFrame:
    """Flat event table for Page 2."""
    with get_connection() as conn:
        coupons = pd.read_sql(
            "SELECT isin, payment_date, scheduled_date, was_rolled, "
            "'COUPON' as event_type, coupon_amount_bdt_mill as amount_bdt_mill, "
            "calc_method, formula_string, coupon_frequency, data_quality, source_page "
            "FROM coupon_events WHERE payment_date BETWEEN ? AND ?",
            conn, params=(date_from.isoformat(), date_to.isoformat())
        )
        mats = pd.read_sql(
            "SELECT isin, payment_date, scheduled_date, was_rolled, "
            "'PRINCIPAL_MATURITY' as event_type, principal_bdt_mill as amount_bdt_mill, "
            "calc_method, formula_string, security_type, data_quality, source_page "
            "FROM maturity_events WHERE payment_date BETWEEN ? AND ?",
            conn, params=(date_from.isoformat(), date_to.isoformat())
        )
        aucs = pd.read_sql(
            "SELECT auction_no as isin, settlement_date as payment_date, "
            "auction_date as scheduled_date, '0' as was_rolled, "
            "'AUCTION' as event_type, best_amount_bdt_mill as amount_bdt_mill, "
            "outflow_status as calc_method, "
            "'' as formula_string, security_type, data_quality, source "
            "FROM auction_events WHERE settlement_date BETWEEN ? AND ?",
            conn, params=(date_from.isoformat(), date_to.isoformat())
        )
    return pd.concat([coupons, mats, aucs], ignore_index=True).sort_values("payment_date")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _records_to_df(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {k: v for k, v in r.items()} for r in records
    ])


def _date_cols_to_str(df: pd.DataFrame, cols: list[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].apply(
                lambda v: v.isoformat() if isinstance(v, date) else v
            )


def _bool_cols_to_int(df: pd.DataFrame, cols: list[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].apply(lambda v: 1 if v else 0)


def _upsert_method(table: str, conflict_cols: str):
    """
    Return a pandas to_sql 'method' callable that does INSERT OR REPLACE.
    """
    def method(pd_table, conn, keys, data_iter):
        from itertools import chain
        cols = ", ".join(keys)
        placeholders = ", ".join(["?"] * len(keys))
        sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
        conn.executemany(sql, data_iter)
    return method
