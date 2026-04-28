"""
config.py  — All constants, source URLs, and runtime settings.
Change values here only — never hardcode in other modules.
"""
import os
import datetime
from pathlib import Path

ROOT      = Path(__file__).parent
SEEDS_DIR = ROOT / "data" / "seeds"
LOGS_DIR  = ROOT / "logs"

# ── Database ──────────────────────────────────────────────────────────────────
# Priority: DATABASE_URL env var (Supabase/any Postgres) → local SQLite fallback.
# Set DATABASE_URL in:
#   - Streamlit Cloud:  App settings → Secrets  →  DATABASE_URL = "postgresql://..."
#   - GitHub Actions:   repo Settings → Secrets  →  DATABASE_URL
#   - Local override:   create a .env file or export the var before running
# Also check Streamlit secrets (when running on Streamlit Cloud or locally with secrets.toml)
def _get_db_url_from_secrets():
    try:
        import streamlit as st
        return st.secrets.get("DATABASE_URL", "")
    except Exception:
        return ""

_db_url_env = os.environ.get("DATABASE_URL", "") or _get_db_url_from_secrets()
if _db_url_env:
    # Supabase (and some other hosts) give "postgres://" — SQLAlchemy needs "postgresql://"
    DB_URL  = _db_url_env.replace("postgres://", "postgresql://", 1)
    DB_PATH = None          # not used with Postgres
else:
    # Local SQLite
    if os.environ.get("HOME") == "/home/adminuser":
        DB_PATH = Path("/tmp/mm_dashboard.db")
    else:
        DB_PATH = ROOT / "data" / "mm_dashboard.db"
    DB_URL = f"sqlite:///{DB_PATH}"

# Source URLs
GSOM_TBOND_URL    = "https://gsom.bb.org.bd/mtm.php"
GSOM_FRTB_URL     = "https://gsom.bb.org.bd/mtm-frtb.php"
GSOM_TBILL_URL    = "https://gsom.bb.org.bd/mtm-bill.php"
GSOM_FRTB_DL_URL  = "https://gsom.bb.org.bd/api/dl_frtb_mtm_data.php"
GSOM_TBOND_DL_URL = "https://gsom.bb.org.bd/api/dl_tbond_mtm_data.php"
GSOM_TBILL_DL_URL = "https://gsom.bb.org.bd/api/dl_tbill_mtm_data.php"
BB_AUC_CALENDAR_URL = "https://www.bb.org.bd/en/index.php/monetaryactivity/auc_calendar/1"
BB_TREASURY_URL     = "https://www.bb.org.bd/en/index.php/monetaryactivity/treasury"
BB_HOLIDAY_URL      = "https://www.bb.org.bd/en/index.php/mediaroom/holiday"
BB_PRESS_URL        = "https://www.bb.org.bd/en/index.php/mediaroom/press_release"

# HTTP
REQUEST_TIMEOUT  = 30
REQUEST_RETRIES  = 3
REQUEST_BACKOFF  = 2
REQUEST_HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Calendar
BD_WORKING_WEEKDAYS = {0, 1, 2, 3, 6}   # Mon-Thu + Sun
BD_WEEKEND_DAYS     = {4, 5}             # Fri=4, Sat=5
MAX_ROLL_DAYS       = 14

# Units
CRORE_TO_MILLION = 10
UNIT_LABEL       = "BDT million"

# Calculation
HFLY_DIVISOR   = 2
QRTY_DIVISOR   = 4
DAYS_IN_YEAR   = 365

# Validation
MAX_COUPON_RATE_PCT    = 30.0
MAX_APPROX_ERROR_PCT   = 1.0
OUTSTANDING_MIN        = 0.01

def fiscal_year(date) -> str:
    d = date if isinstance(date, datetime.date) else date.date()
    if d.month >= 7:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"
