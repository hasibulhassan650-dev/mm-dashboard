"""
dashboard/app.py — Streamlit Money Market Dashboard.
Run: python -m streamlit run dashboard/app.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime
import logging
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from io import BytesIO

from db import (init_db, get_session, DailyNetFlow, CouponEvent,
                MaturityEvent, AuctionEvent, Security, HolidayCalendar)
from calendar_utils import load_holidays, is_working_day
from engines.pipeline import run_pipeline
from dashboard.date_utils import safe_date_input_value, safe_date_range, clamp_date_to_data

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BD Money Market Dashboard",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stDataFrame { font-size: 13px; }
div[data-testid="metric-container"] { background:#f0f4fa; border-radius:8px; padding:8px; }
</style>
""", unsafe_allow_html=True)


# ── Initialise DB and holidays (once per process) ─────────────────────────────
@st.cache_resource
def _init():
    init_db()
    session = get_session()
    hrows = session.query(HolidayCalendar).all()
    load_holidays({r.calendar_date for r in hrows})
    session.close()

_init()

today = datetime.date.today()


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS (cached)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_daily_flows(date_from, date_to):
    session = get_session()
    rows = session.query(DailyNetFlow).filter(
        DailyNetFlow.flow_date >= date_from,
        DailyNetFlow.flow_date <= date_to,
    ).order_by(DailyNetFlow.flow_date).all()
    session.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "Date":                r.flow_date,
        "Maturity Inflow":     r.principal_inflow_bdt_mill   or 0.0,
        "Coupon Inflow":       r.coupon_inflow_bdt_mill       or 0.0,
        "Total Inflow":        r.total_inflow_bdt_mill        or 0.0,
        "Auction Outflow":     r.auction_outflow_best_mill    or 0.0,
        "Outflow (Planned)":   r.auction_outflow_planned_mill or 0.0,
        "Outflow (Confirmed)": r.auction_outflow_confirmed_mill or 0.0,
        "Net Borrowing":       r.net_borrowing_bdt_mill       or 0.0,
        "Maturing ISINs":      r.inflow_security_count        or 0,
        "Coupon Count":        r.coupon_payment_count         or 0,
        "Data Complete":       bool(r.data_complete),
    } for r in rows])
    df["Date"] = pd.to_datetime(df["Date"])
    df["Cumulative Net Borrowing"] = df["Net Borrowing"].cumsum()
    return df


@st.cache_data(ttl=300)
def load_events(date_from, date_to, event_types, instrument_types):
    session = get_session()
    rows = []
    use_all = "ALL" in instrument_types

    if "COUPON" in event_types:
        q = session.query(CouponEvent, Security).join(
            Security, CouponEvent.isin == Security.isin, isouter=True
        ).filter(CouponEvent.payment_date >= date_from,
                 CouponEvent.payment_date <= date_to)
        if not use_all:
            q = q.filter(Security.security_type.in_(instrument_types))
        for ce, sec in q.all():
            rows.append({
                "Payment Date":    ce.payment_date,
                "ISIN":            ce.isin,
                "Security Name":   sec.security_name_norm if sec else ce.isin,
                "Instrument":      sec.security_type if sec else "?",
                "Event Type":      "COUPON",
                "Amount (BDT mn)": ce.amount_bdt_mill,
                "Calc Method":     ce.calc_method,
                "Formula":         ce.formula_string,
                "Data Quality":    ce.data_quality,
                "Source":          sec.source_page if sec else "",
            })

    if "MATURITY" in event_types:
        q = session.query(MaturityEvent, Security).join(
            Security, MaturityEvent.isin == Security.isin, isouter=True
        ).filter(MaturityEvent.payment_date >= date_from,
                 MaturityEvent.payment_date <= date_to)
        if not use_all:
            q = q.filter(Security.security_type.in_(instrument_types))
        for me, sec in q.all():
            rows.append({
                "Payment Date":    me.payment_date,
                "ISIN":            me.isin,
                "Security Name":   sec.security_name_norm if sec else me.isin,
                "Instrument":      sec.security_type if sec else "?",
                "Event Type":      "MATURITY",
                "Amount (BDT mn)": me.principal_bdt_mill,
                "Calc Method":     me.calc_method,
                "Formula":         me.formula_string,
                "Data Quality":    me.data_quality,
                "Source":          sec.source_page if sec else "",
            })

    if "AUCTION" in event_types:
        q = session.query(AuctionEvent).filter(
            AuctionEvent.settlement_date >= date_from,
            AuctionEvent.settlement_date <= date_to,
        )
        if not use_all:
            q = q.filter(AuctionEvent.security_type.in_(instrument_types))
        for ae in q.all():
            rows.append({
                "Payment Date":    ae.settlement_date,
                "ISIN":            ae.resulting_isin or "",
                "Security Name":   f"Auction {ae.auction_no}/{ae.fiscal_year} {ae.tenor_label}",
                "Instrument":      ae.security_type,
                "Event Type":      "AUCTION",
                "Amount (BDT mn)": ae.offered_amount_bdt_mill,
                "Calc Method":     ae.outflow_status,
                "Formula":         (
                    f"{ae.offered_amount_bdt_crore} crore × 10 = "
                    f"{ae.offered_amount_bdt_mill} mn"
                ),
                "Data Quality":    ae.data_quality,
                "Source":          ae.source,
            })

    session.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["Payment Date"] = pd.to_datetime(df["Payment Date"])
    return df.sort_values("Payment Date").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_securities(instrument_types):
    session = get_session()
    q = session.query(Security)
    if "ALL" not in instrument_types:
        q = q.filter(Security.security_type.in_(instrument_types))
    rows = q.order_by(Security.maturity_date).all()
    session.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "ISIN":             r.isin,
        "Security Name":    r.security_name_norm,
        "Instrument":       r.security_type,
        "Issue Date":       r.issue_date,
        "Maturity Date":    r.maturity_date,
        "Coupon Rate (%)":  r.coupon_rate_pct,
        "Frequency":        r.coupon_frequency,
        "Outstanding (mn)": r.outstanding_bdt_mill,
        "Days to Maturity": (r.maturity_date - today).days if r.maturity_date else None,
        "Snapshot Date":    r.source_settlement_date,
        "Data Quality":     r.data_quality,
    } for r in rows])


@st.cache_data(ttl=300)
def load_auction_debug(date_from, date_to):
    """Load auction events with full debug fields for the quality tab."""
    session = get_session()
    rows = session.query(AuctionEvent).filter(
        AuctionEvent.settlement_date >= date_from,
        AuctionEvent.settlement_date <= date_to,
    ).order_by(AuctionEvent.auction_date, AuctionEvent.tenor_label).all()
    session.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Auction Date":          ae.auction_date,
        "Adjusted Outflow Date": ae.settlement_date,
        "Instrument":            ae.security_type,
        "Tenor":                 ae.tenor_label,
        "Auction No":            ae.auction_no,
        "FY":                    ae.fiscal_year,
        "Offered (crore)":       ae.offered_amount_bdt_crore,
        "Offered (mn)":          ae.offered_amount_bdt_mill,
        "Accepted (mn)":         ae.accepted_amount_bdt_mill,
        "Status":                ae.outflow_status,
        "Roll Days":             ae.roll_days,
        "Adjustment Reason":     ae.roll_reason,
        "Data Quality":          ae.data_quality,
        "Source":                ae.source,
    } for ae in rows])


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL FILTER SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

st.sidebar.title("🏦 BD Money Market")
st.sidebar.markdown("---")

# ── Determine safe default date range from DB ─────────────────────────────────
@st.cache_data(ttl=300)
def _get_data_date_range():
    """Return (min_date, max_date) of flow_date in the DB, or None."""
    session = get_session()
    from sqlalchemy import func
    result = session.query(
        func.min(DailyNetFlow.flow_date),
        func.max(DailyNetFlow.flow_date),
    ).first()
    session.close()
    if result and result[0] and result[1]:
        return result[0], result[1]
    return None

db_range = _get_data_date_range()

# Build safe defaults
if db_range:
    db_min, db_max = db_range
    default_from = safe_date_input_value(
        today - datetime.timedelta(days=30), db_min, db_max
    )
    default_to = safe_date_input_value(
        today + datetime.timedelta(days=90), db_min, db_max
    )
    sidebar_min = db_min
    sidebar_max = db_max
else:
    # No data yet — use wide defaults so widgets still render
    default_from = today - datetime.timedelta(days=30)
    default_to   = today + datetime.timedelta(days=90)
    sidebar_min  = today - datetime.timedelta(days=365)
    sidebar_max  = today + datetime.timedelta(days=730)

date_from = st.sidebar.date_input(
    "From",
    value=default_from,
    min_value=sidebar_min,
    max_value=sidebar_max,
)
date_to = st.sidebar.date_input(
    "To",
    value=default_to,
    min_value=sidebar_min,
    max_value=sidebar_max,
)

# Ensure from <= to
if date_from > date_to:
    st.sidebar.error("'From' date must be before 'To' date.")
    date_from, date_to = date_to, date_from

instrument_opts = ["ALL", "T_BILL", "T_BOND", "FRTB"]
instruments = st.sidebar.multiselect("Instrument", instrument_opts, default=["ALL"])
if not instruments:
    instruments = ["ALL"]

event_opts  = ["COUPON", "MATURITY", "AUCTION"]
event_types = st.sidebar.multiselect("Event Type", event_opts, default=event_opts)

status_filter = st.sidebar.selectbox("Auction Status", ["ALL", "CONFIRMED", "PLANNED"])

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Refresh Data (Run Pipeline)", use_container_width=True):
    with st.spinner("Fetching live data and recomputing…"):
        st.cache_data.clear()
        summary = run_pipeline(date_from, date_to)
        if summary.get("errors"):
            st.sidebar.error("Errors: " + "; ".join(summary["errors"]))
        else:
            st.sidebar.success(
                f"✓ Securities: {summary['securities']}  "
                f"Coupons: {summary['coupons']}  "
                f"Maturities: {summary['maturities']}  "
                f"Auctions: {summary['auctions']}"
            )
        st.rerun()

st.sidebar.markdown("---")
if not db_range:
    st.sidebar.warning("No data yet. Click Refresh Data to run the pipeline.")
else:
    st.sidebar.caption(f"Data: {db_min} → {db_max}")


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📅 Daily Summary",
    "📋 Event Table",
    "🔒 Securities",
    "🔍 Date Drilldown",
    "⚠️ Data Quality",
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Daily Summary
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Daily Cash Flow Summary")

    if not db_range:
        st.info("No data loaded yet. Click **Refresh Data** in the sidebar.")
    else:
        df = load_daily_flows(date_from, date_to)

        if df.empty:
            st.warning("No daily flow data in selected range. Try widening the date filter.")
        else:
            # ── KPI strip ─────────────────────────────────────────────────────
            nearest = df[df["Date"] <= pd.Timestamp(today)]
            row = nearest.iloc[-1] if not nearest.empty else df.iloc[0]

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Total Inflow (mn)",     f"৳{row['Total Inflow']:,.0f}")
            k2.metric("Auction Outflow (mn)",  f"৳{row['Auction Outflow']:,.0f}")
            net = row["Net Borrowing"]
            k3.metric("Net Borrowing (mn)",    f"৳{net:,.0f}",
                      delta="Net borrower" if net > 0 else "Net repayer",
                      delta_color="inverse" if net > 0 else "normal")
            k4.metric("Data Complete", "✓" if row["Data Complete"] else "⚠ Partial")

            # ── Chart ─────────────────────────────────────────────────────────
            fig = go.Figure()
            fig.add_trace(go.Bar(x=df["Date"], y=df["Maturity Inflow"],
                                 name="Maturity Inflow", marker_color="#1f6feb"))
            fig.add_trace(go.Bar(x=df["Date"], y=df["Coupon Inflow"],
                                 name="Coupon Inflow", marker_color="#0d9488"))
            fig.add_trace(go.Bar(x=df["Date"], y=-df["Auction Outflow"],
                                 name="Auction Outflow", marker_color="#cf222e",
                                 opacity=0.75))
            fig.add_trace(go.Scatter(x=df["Date"], y=df["Net Borrowing"],
                                     name="Net Borrowing", mode="lines+markers",
                                     line=dict(color="#d1780f", width=2),
                                     yaxis="y2"))
            fig.update_layout(
                barmode="relative",
                yaxis=dict(title="BDT million"),
                yaxis2=dict(title="Net Borrowing", overlaying="y", side="right"),
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                height=430, plot_bgcolor="#fff", paper_bgcolor="#fff",
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── Table ─────────────────────────────────────────────────────────
            disp = df.copy()
            disp["Date"] = disp["Date"].dt.strftime("%d-%b-%Y")
            for c in ["Maturity Inflow","Coupon Inflow","Total Inflow","Auction Outflow"]:
                disp[c] = disp[c].apply(lambda v: f"{v:,.0f}")
            disp["Net Borrowing"] = df["Net Borrowing"].apply(
                lambda v: f"{'▲' if v>0 else '▼'} {v:,.0f}")
            disp["✓"] = df["Data Complete"].apply(lambda v: "✓" if v else "⚠")
            st.dataframe(
                disp[["Date","Maturity Inflow","Coupon Inflow","Total Inflow",
                       "Auction Outflow","Net Borrowing","Maturing ISINs",
                       "Coupon Count","✓"]],
                use_container_width=True, hide_index=True,
            )

            # ── Export ────────────────────────────────────────────────────────
            ea, eb = st.columns([1, 1])
            with ea:
                st.download_button("⬇ CSV", df.to_csv(index=False).encode(),
                    f"mm-{today:%Y%m%d}-daily.csv", "text/csv")
            with eb:
                buf = BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    df.to_excel(w, index=False, sheet_name="Daily")
                st.download_button("⬇ Excel", buf.getvalue(),
                    f"mm-{today:%Y%m%d}-daily.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Event Table
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("All Cash Flow Events")
    edf = load_events(date_from, date_to, event_types, instruments)

    if edf.empty:
        st.info("No events in selected range. Adjust filters or run the pipeline.")
    else:
        search = st.text_input("🔍 Search ISIN or name")
        if search:
            mask = (edf["ISIN"].str.contains(search, case=False, na=False) |
                    edf["Security Name"].str.contains(search, case=False, na=False))
            edf = edf[mask]

        if status_filter != "ALL":
            edf = edf[edf["Calc Method"] == status_filter]

        # Summary counts
        bc1, bc2, bc3 = st.columns(3)
        bc1.metric("Total events", len(edf))
        bc2.metric("Auction rows", len(edf[edf["Event Type"]=="AUCTION"]))
        bc3.metric("Inflow rows",  len(edf[edf["Event Type"].isin(["COUPON","MATURITY"])]))

        st.dataframe(
            edf.assign(**{"Payment Date": edf["Payment Date"].dt.strftime("%d-%b-%Y")}),
            use_container_width=True, hide_index=True, height=440,
        )
        st.download_button("⬇ Export CSV",
            edf.to_csv(index=False).encode(),
            f"mm-{today:%Y%m%d}-events.csv", "text/csv")


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Securities
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("Security Master")
    sdf = load_securities(instruments)

    if sdf.empty:
        st.info("No securities loaded. Run the pipeline first.")
    else:
        # ── Maturity date filter with safe defaults ────────────────────────────
        mat_dates = pd.to_datetime(sdf["Maturity Date"]).dropna()
        mat_range = safe_date_range(mat_dates.dt.date.tolist())

        if mat_range:
            mat_min, mat_max = mat_range
            mf_default = safe_date_input_value(today, mat_min, mat_max)
            mt_default = safe_date_input_value(today + datetime.timedelta(days=730), mat_min, mat_max)

            mc1, mc2 = st.columns(2)
            with mc1:
                mf = st.date_input("Maturity from", value=mf_default,
                                   min_value=mat_min, max_value=mat_max, key="sec_mf")
            with mc2:
                mt = st.date_input("Maturity to",   value=mt_default,
                                   min_value=mat_min, max_value=mat_max, key="sec_mt")

            sdf = sdf[
                (pd.to_datetime(sdf["Maturity Date"]).dt.date >= mf) &
                (pd.to_datetime(sdf["Maturity Date"]).dt.date <= mt)
            ]

        st.caption(f"{len(sdf):,} securities")
        st.dataframe(
            sdf[["ISIN","Security Name","Instrument","Issue Date","Maturity Date",
                 "Coupon Rate (%)","Frequency","Outstanding (mn)",
                 "Days to Maturity","Data Quality"]],
            use_container_width=True, hide_index=True, height=450,
        )
        st.download_button("⬇ Export CSV",
            sdf.to_csv(index=False).encode(),
            f"mm-{today:%Y%m%d}-securities.csv", "text/csv")


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — Date Drilldown
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("Date Drilldown")

    # ── Safe date picker ──────────────────────────────────────────────────────
    if not db_range:
        st.info("No data loaded yet.")
    else:
        drill_min = db_range[0]
        drill_max = db_range[1]
        drill_default = safe_date_input_value(today, drill_min, drill_max)

        # Honour session state from prev/next navigation
        if "drill_date" in st.session_state:
            saved = st.session_state["drill_date"]
            if isinstance(saved, datetime.date):
                drill_default = safe_date_input_value(saved, drill_min, drill_max)

        drill_date = st.date_input(
            "Select date",
            value=drill_default,
            min_value=drill_min,
            max_value=drill_max,
            key="drill_date_widget",
        )

        # Prev / Next working-day navigation
        nav1, nav2, _ = st.columns([1, 1, 10])
        with nav1:
            if st.button("◀ Prev"):
                d = drill_date - datetime.timedelta(days=1)
                while d >= drill_min and not is_working_day(d):
                    d -= datetime.timedelta(days=1)
                st.session_state["drill_date"] = safe_date_input_value(d, drill_min, drill_max)
                st.rerun()
        with nav2:
            if st.button("Next ▶"):
                d = drill_date + datetime.timedelta(days=1)
                while d <= drill_max and not is_working_day(d):
                    d += datetime.timedelta(days=1)
                st.session_state["drill_date"] = safe_date_input_value(d, drill_min, drill_max)
                st.rerun()

        dd = drill_date
        session = get_session()
        mats  = session.query(MaturityEvent, Security).join(
            Security, MaturityEvent.isin == Security.isin, isouter=True
        ).filter(MaturityEvent.payment_date == dd).all()
        coups = session.query(CouponEvent, Security).join(
            Security, CouponEvent.isin == Security.isin, isouter=True
        ).filter(CouponEvent.payment_date == dd).all()
        aucs  = session.query(AuctionEvent).filter(
            AuctionEvent.settlement_date == dd).all()
        session.close()

        mat_total  = sum(m.principal_bdt_mill or 0 for m, _ in mats)
        coup_total = sum(c.amount_bdt_mill    or 0 for c, _ in coups)
        auc_total  = sum(
            (a.accepted_amount_bdt_mill or a.offered_amount_bdt_mill or 0)
            for a in aucs
        )
        # Split by instrument for the auction total
        bill_out = sum(
            (a.accepted_amount_bdt_mill or a.offered_amount_bdt_mill or 0)
            for a in aucs if a.security_type == "T_BILL"
        )
        bond_out = sum(
            (a.accepted_amount_bdt_mill or a.offered_amount_bdt_mill or 0)
            for a in aucs if a.security_type in ("T_BOND", "FRTB")
        )
        net = auc_total - mat_total - coup_total

        # ── Summary metrics ────────────────────────────────────────────────────
        s1,s2,s3,s4,s5,s6 = st.columns(6)
        s1.metric("Maturity Inflow",    f"৳{mat_total:,.0f}")
        s2.metric("Coupon Inflow",      f"৳{coup_total:,.0f}")
        s3.metric("Total Inflow",       f"৳{mat_total+coup_total:,.0f}")
        s4.metric("T-Bill Outflow",     f"৳{bill_out:,.0f}")
        s5.metric("Bond/FRTB Outflow",  f"৳{bond_out:,.0f}")
        s6.metric("Net Borrowing",      f"৳{net:,.0f}",
                  delta_color="inverse" if net > 0 else "normal")

        # ── Waterfall ─────────────────────────────────────────────────────────
        if mat_total + coup_total + auc_total > 0:
            wf = go.Figure(go.Waterfall(
                orientation="v",
                measure=["relative","relative","relative","total"],
                x=["Maturity Inflow","Coupon Inflow","Auction Outflow (−)","Net Borrowing"],
                y=[mat_total, coup_total, -auc_total, 0],
                textposition="outside",
                text=[f"{mat_total:,.0f}",f"{coup_total:,.0f}",
                      f"{auc_total:,.0f}",f"{net:,.0f}"],
                connector={"line":{"color":"#666"}},
                decreasing={"marker":{"color":"#cf222e"}},
                increasing={"marker":{"color":"#1a7f37"}},
            ))
            wf.update_layout(height=280, showlegend=False,
                             title=dd.strftime("%d %b %Y — Cash Flow Waterfall"))
            st.plotly_chart(wf, use_container_width=True)

        # ── Sub-tables ────────────────────────────────────────────────────────
        d1, d2, d3 = st.columns(3)

        with d1:
            st.subheader(f"Maturities ({len(mats)})")
            if mats:
                st.dataframe(pd.DataFrame([{
                    "ISIN":       m.isin,
                    "Name":       (s.security_name_norm if s else m.isin),
                    "Type":       (s.security_type if s else ""),
                    "Sched.":     m.scheduled_date.strftime("%d-%b-%Y") if m.scheduled_date else "",
                    "Principal":  f"{m.principal_bdt_mill:,.2f}",
                    "Roll":       m.roll_days,
                } for m,s in mats]), use_container_width=True, hide_index=True)
            else:
                st.caption("No maturities on this date")

        with d2:
            st.subheader(f"Coupons ({len(coups)})")
            if coups:
                st.dataframe(pd.DataFrame([{
                    "ISIN":     c.isin,
                    "Name":     (s.security_name_norm if s else c.isin),
                    "Rate %":   c.coupon_rate_used_pct,
                    "Sched.":   c.scheduled_date.strftime("%d-%b-%Y") if c.scheduled_date else "",
                    "Amount":   f"{c.amount_bdt_mill:,.4f}",
                    "Formula":  c.formula_string,
                } for c,s in coups]), use_container_width=True, hide_index=True)
            else:
                st.caption("No coupon payments on this date")

        with d3:
            st.subheader(f"Auctions ({len(aucs)})")
            if aucs:
                st.dataframe(pd.DataFrame([{
                    "Auction Date": a.auction_date.strftime("%d-%b-%Y") if a.auction_date else "",
                    "Tenor":        a.tenor_label,
                    "Type":         a.security_type,
                    "Offered mn":   f"{a.offered_amount_bdt_mill:,.0f}",
                    "Accepted mn":  f"{a.accepted_amount_bdt_mill:,.0f}" if a.accepted_amount_bdt_mill else "PLANNED",
                    "Status":       a.outflow_status,
                    "Roll Days":    a.roll_days,
                    "Reason":       a.roll_reason,
                } for a in aucs]), use_container_width=True, hide_index=True)
            else:
                st.caption("No auction settlements on this date")


# ════════════════════════════════════════════════════════════════════════════
# TAB 5 — Data Quality
# ════════════════════════════════════════════════════════════════════════════
with tab5:
    st.header("Data Quality & Auction Debug")

    session = get_session()
    bad_sec  = session.query(Security).filter(Security.data_quality != "OK").all()
    bad_coup = session.query(CouponEvent).filter(CouponEvent.data_quality != "OK").limit(200).all()
    bad_mat  = session.query(MaturityEvent).filter(MaturityEvent.data_quality != "OK").all()
    stale    = session.query(AuctionEvent).filter(
        AuctionEvent.outflow_status == "PLANNED",
        AuctionEvent.settlement_date < today,
    ).all()
    holidays = session.query(HolidayCalendar).order_by(HolidayCalendar.calendar_date).all()
    session.close()

    q1,q2,q3,q4 = st.columns(4)
    q1.metric("Flagged Securities",    len(bad_sec))
    q2.metric("Flagged Coupons",       len(bad_coup))
    q3.metric("Flagged Maturities",    len(bad_mat))
    q4.metric("Stale Planned Auctions",len(stale))

    # ── Auction debug table (the key new requirement) ─────────────────────────
    st.subheader("Auction Outflow Debug — Bills & Bonds")
    adf = load_auction_debug(date_from, date_to)
    if adf.empty:
        st.info("No auction events in selected date range.")
    else:
        bill_df = adf[adf["Instrument"] == "T_BILL"]
        bond_df = adf[adf["Instrument"].isin(["T_BOND","FRTB"])]

        st.markdown(f"**T-Bill rows:** {len(bill_df)} &nbsp;|&nbsp; "
                    f"**T-Bond/FRTB rows:** {len(bond_df)} &nbsp;|&nbsp; "
                    f"**Total:** {len(adf)}")

        # Verification: no settlement on weekend
        bad_settle = adf[pd.to_datetime(adf["Adjusted Outflow Date"]).dt.dayofweek.isin([4,5])]
        if not bad_settle.empty:
            st.error(f"⛔ {len(bad_settle)} auction rows have outflow on a weekend!")
            st.dataframe(bad_settle, use_container_width=True, hide_index=True)
        else:
            st.success("✓ All auction outflow dates are valid working days (not Fri/Sat)")

        with st.expander("T-Bill auction rows", expanded=True):
            st.dataframe(
                bill_df[["Auction Date","Adjusted Outflow Date","Tenor",
                         "Offered (crore)","Offered (mn)","Roll Days",
                         "Adjustment Reason","Status"]],
                use_container_width=True, hide_index=True,
            )
        with st.expander("T-Bond / FRTB auction rows", expanded=True):
            st.dataframe(
                bond_df[["Auction Date","Adjusted Outflow Date","Tenor",
                          "Offered (crore)","Offered (mn)","Roll Days",
                          "Adjustment Reason","Status"]],
                use_container_width=True, hide_index=True,
            )

    # ── Holiday calendar ───────────────────────────────────────────────────────
    st.subheader("Holiday Calendar")
    if holidays:
        hdf = pd.DataFrame([{
            "Date":  h.calendar_date.strftime("%d-%b-%Y"),
            "Name":  h.holiday_name,
            "Type":  h.holiday_type,
            "FY":    h.fiscal_year,
        } for h in holidays])
        st.dataframe(hdf, use_container_width=True, hide_index=True)
    else:
        st.warning(
            "No holidays loaded. Run: "
            "`python seeds_loader.py --file data/seeds/holidays_2025-26.yaml`"
        )

    # ── Flagged securities ─────────────────────────────────────────────────────
    if bad_sec:
        st.subheader("Flagged Securities")
        st.dataframe(pd.DataFrame([{
            "ISIN": s.isin, "Name": s.security_name_norm, "Flag": s.data_quality
        } for s in bad_sec]), hide_index=True)

    # ── Min/max date diagnostic ────────────────────────────────────────────────
    st.subheader("Date Range Diagnostics")
    st.json({
        "db_data_range":       str(db_range),
        "sidebar_from":        str(date_from),
        "sidebar_to":          str(date_to),
        "drill_default_used":  str(safe_date_input_value(today,
                                   db_range[0] if db_range else today,
                                   db_range[1] if db_range else today)),
        "today":               str(today),
    })
