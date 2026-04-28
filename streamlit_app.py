import sys, os, pathlib, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(
    page_title="BD Money Market Dashboard",
    page_icon="🏦",
    layout="wide",
)

# ── Run pipeline once on first load, show progress to user ───────────────────
@st.cache_resource(show_spinner=False)
def initialise():
    """Run once per server session. Cached so it does not repeat on page reload."""
    from db import init_db
    from seeds_loader import load_holiday_file
    from engines.pipeline import run_pipeline
    from calendar_utils import load_holidays

    init_db()

    seed = pathlib.Path("data/seeds/holidays_2025-26.yaml")
    if seed.exists():
        load_holiday_file(str(seed))

    # Load holidays into memory
    from db import get_session, HolidayCalendar
    session = get_session()
    rows = session.query(HolidayCalendar).all()
    load_holidays({r.calendar_date for r in rows})
    session.close()

    summary = run_pipeline(
        datetime.date.today() - datetime.timedelta(days=60),
        datetime.date.today() + datetime.timedelta(days=120),
    )
    return summary

# Show spinner while initialising
with st.spinner("Loading data — please wait up to 60 seconds on first visit..."):
    summary = initialise()

if summary.get("errors"):
    st.warning(f"Pipeline completed with warnings: {summary['errors']}")

# ── Load the main dashboard ───────────────────────────────────────────────────
from dashboard.app import render
render()
