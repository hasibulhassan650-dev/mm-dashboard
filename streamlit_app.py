import sys, os, pathlib, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(
    page_title="BD Money Market Dashboard",
    page_icon="🏦",
    layout="wide",
)

# ── Run pipeline once per day, cached by date so it auto-refreshes at midnight ─
@st.cache_resource(show_spinner=False)
def initialise(today: datetime.date):
    """Run once per calendar day. Re-runs automatically when the date changes."""
    from db import init_db
    from seeds_loader import load_holiday_file
    from engines.pipeline import run_pipeline
    from calendar_utils import load_holidays

    init_db()

    seed = pathlib.Path("data/seeds/holidays_2025-26.yaml")
    if seed.exists():
        load_holiday_file(str(seed))

    from db import get_session, HolidayCalendar
    session = get_session()
    rows = session.query(HolidayCalendar).all()
    load_holidays({r.calendar_date for r in rows})
    session.close()

    summary = run_pipeline(
        today - datetime.timedelta(days=60),
        today + datetime.timedelta(days=120),
    )
    return summary

# ── Only show spinner on actual first load, not on every widget interaction ───
today = datetime.date.today()
if "init_done" not in st.session_state:
    with st.spinner("Loading today's data — please wait up to 60 seconds on first visit..."):
        summary = initialise(today)
    st.session_state["init_done"] = True
    st.session_state["init_errors"] = summary.get("errors", [])
else:
    summary = initialise(today)  # returns instantly from cache

if st.session_state.get("init_errors"):
    st.warning(f"Pipeline completed with warnings: {st.session_state['init_errors']}")

# ── Load the main dashboard ───────────────────────────────────────────────────
from dashboard.app import render
render()
