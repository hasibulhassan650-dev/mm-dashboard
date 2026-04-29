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
                MaturityEvent, AuctionEvent, Security, HolidayCalendar,
                MtmSnapshot, PrimaryYieldSnapshot, OMOTransaction)
from calendar_utils import load_holidays, is_working_day
from engines.pipeline import run_pipeline, run_primary_yield_history, run_omo_fetch
from dashboard.date_utils import safe_date_input_value, safe_date_range, clamp_date_to_data

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS (cached — must stay at module level)
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
    today = datetime.date.today()
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


@st.cache_data(ttl=300)
def _get_data_date_range():
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


@st.cache_data(ttl=300)
def load_yield_curve():
    """Return (DataFrame, settlement_date) for the latest MTM snapshot."""
    today = datetime.date.today()
    session = get_session()
    from sqlalchemy import func
    latest = session.query(func.max(MtmSnapshot.settlement_date)).scalar()
    if not latest:
        session.close()
        return pd.DataFrame(), None

    rows = (
        session.query(MtmSnapshot, Security)
        .join(Security, MtmSnapshot.isin == Security.isin, isouter=True)
        .filter(MtmSnapshot.settlement_date == latest)
        .all()
    )
    session.close()

    data = []
    for snap, sec in rows:
        if snap.market_yield_pct is None:
            continue
        mat = sec.maturity_date if (sec and sec.maturity_date) else None
        if mat is None:
            continue
        rem_years = (mat - today).days / 365.25
        if rem_years < 0:
            continue
        stype = sec.security_type if sec else "UNKNOWN"
        data.append({
            "ISIN":                      snap.isin,
            "Type":                      stype,
            "Security Name":             sec.security_name_norm if sec else snap.isin,
            "Maturity Date":             mat,
            "Remaining Maturity (Yrs)":  round(rem_years, 3),
            "Yield (%)":                 snap.market_yield_pct,
            "Outstanding (mn)":          snap.outstanding_bdt_mill,
            "Settlement Date":           snap.settlement_date,
        })

    if not data:
        return pd.DataFrame(), latest
    df = pd.DataFrame(data).sort_values("Remaining Maturity (Yrs)").reset_index(drop=True)
    return df, latest


@st.cache_data(ttl=300)
def load_primary_yield_curve():
    """Return DataFrame of latest primary market yield per tenor (one point per tenor)."""
    session = get_session()
    from sqlalchemy import func
    # Latest snapshot_date per tenor (most recent auction result per tenor)
    subq = (
        session.query(
            PrimaryYieldSnapshot.tenor_label,
            func.max(PrimaryYieldSnapshot.auction_date).label("max_ad"),
        )
        .group_by(PrimaryYieldSnapshot.tenor_label)
        .subquery()
    )
    rows = (
        session.query(PrimaryYieldSnapshot)
        .join(subq, (PrimaryYieldSnapshot.tenor_label  == subq.c.tenor_label) &
                    (PrimaryYieldSnapshot.auction_date == subq.c.max_ad))
        .all()
    )
    session.close()

    if not rows:
        return pd.DataFrame()

    data = []
    for r in rows:
        if r.cutoff_yield_pct is None or r.tenor_years is None:
            continue
        data.append({
            "Type":                  r.security_type,
            "Tenor":                 r.tenor_label,
            "Tenor (Yrs)":           r.tenor_years,
            "Yield (%)":             r.cutoff_yield_pct,
            "Auction Date":          r.auction_date,
            "Offered (crore)":       r.offered_bdt_crore,
            "Accepted (crore)":      r.accepted_bdt_crore,
            "Source":                r.source,
        })

    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data).sort_values("Tenor (Yrs)").reset_index(drop=True)


@st.cache_data(ttl=300)
def load_primary_yield_history():
    """Official BB Treasury cutoff yields from PrimaryYieldSnapshot (all months stored)."""
    session = get_session()
    rows = (
        session.query(PrimaryYieldSnapshot)
        .filter(PrimaryYieldSnapshot.cutoff_yield_pct.isnot(None))
        .order_by(PrimaryYieldSnapshot.tenor_label, PrimaryYieldSnapshot.auction_date)
        .all()
    )
    session.close()
    if not rows:
        return pd.DataFrame()
    data = [{
        "Type":         r.security_type,
        "Tenor":        r.tenor_label,
        "Auction Date": r.auction_date,
        "Yield (%)":    r.cutoff_yield_pct,
        "Offered (cr)": r.offered_bdt_crore,
        "Accepted (cr)": r.accepted_bdt_crore,
        "Source":       "BB Treasury",
    } for r in rows]
    return pd.DataFrame(data).sort_values(["Tenor","Auction Date"]).reset_index(drop=True)


@st.cache_data(ttl=300)
def load_gsom_yield_trend():
    """For each outstanding security: issue_date, market_yield_pct, original_tenor_class.
    Uses latest MtmSnapshot settlement_date joined with Security issue_date.
    Gives the full historical spread: how yields at issuance have changed across vintages."""
    today = datetime.date.today()
    session = get_session()
    from sqlalchemy import func

    # Use latest settlement date only (most current market yields)
    latest_sd = session.query(func.max(MtmSnapshot.settlement_date)).scalar()
    if not latest_sd:
        session.close()
        return pd.DataFrame()

    rows = (
        session.query(MtmSnapshot, Security)
        .join(Security, MtmSnapshot.isin == Security.isin, isouter=True)
        .filter(
            MtmSnapshot.settlement_date == latest_sd,
            MtmSnapshot.market_yield_pct.isnot(None),
        )
        .all()
    )
    session.close()

    _TENOR_BUCKETS = [
        (0,    0.08,  "14D",  "T_BILL"),
        (0.08, 0.20,  "91D",  "T_BILL"),
        (0.20, 0.40,  "182D", "T_BILL"),
        (0.40, 1.20,  "364D", "T_BILL"),
        (1.20, 3.50,  "2Y",   "T_BOND"),
        (3.50, 6.50,  "5Y",   "T_BOND"),
        (6.50, 11.0,  "10Y",  "T_BOND"),
        (11.0, 17.0,  "15Y",  "T_BOND"),
        (17.0, 25.0,  "20Y",  "T_BOND"),
        (2.50, 3.50,  "3Y_FRTB", "FRTB"),
    ]

    def _classify(issue_date, maturity_date, stype):
        if not (issue_date and maturity_date):
            return None, None
        orig_yrs = (maturity_date - issue_date).days / 365.25
        for lo, hi, lbl, st in _TENOR_BUCKETS:
            if lo <= orig_yrs < hi:
                return lbl, st
        return None, None

    data = []
    for snap, sec in rows:
        if not sec or not sec.issue_date or not sec.maturity_date:
            continue
        lbl, st = _classify(sec.issue_date, sec.maturity_date, sec.security_type)
        if not lbl:
            continue
        data.append({
            "ISIN":           snap.isin,
            "Security Name":  sec.security_name_norm,
            "Type":           st,
            "Tenor Class":    lbl,
            "Issue Date":     sec.issue_date,
            "Maturity Date":  sec.maturity_date,
            "Market Yield (%)": snap.market_yield_pct,
            "Outstanding (mn)": snap.outstanding_bdt_mill,
            "Settlement Date":  snap.settlement_date,
        })

    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data).sort_values(["Type", "Tenor Class", "Issue Date"]).reset_index(drop=True)


@st.cache_data(ttl=300)
def load_omo_transactions(days_back: int = 60):
    """Load all OMO transactions from DB (wider window to capture long-tenor outstanding)."""
    session = get_session()
    # Pull transactions from further back so long-tenors (28D) are included
    cutoff = datetime.date.today() - datetime.timedelta(days=max(days_back, 60))
    rows = (
        session.query(OMOTransaction)
        .filter(OMOTransaction.transaction_date >= cutoff)
        .order_by(OMOTransaction.transaction_date, OMOTransaction.instrument)
        .all()
    )
    session.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "Transaction Date": r.transaction_date,
        "Maturity Date":    r.maturity_date,
        "Instrument":       r.instrument,
        "Tenor":            r.tenor_label,
        "Tenor Days":       r.tenor_days,
        "Amount (Cr)":      r.accepted_bdt_crore,
        "Rate (%)":         r.rate_pct,
        "Direction":        r.direction or "",
        "Source":           r.source_pdf or "",
    } for r in rows])


def _save_primary_yields(rows: list):
    """Persist a list of yield-point dicts to PrimaryYieldSnapshot."""
    from sqlalchemy import select
    session = get_session()
    now = datetime.datetime.utcnow()
    saved = 0
    for row in rows:
        existing = session.execute(
            select(PrimaryYieldSnapshot).where(
                PrimaryYieldSnapshot.tenor_label  == row["tenor_label"],
                PrimaryYieldSnapshot.auction_date == row["auction_date"],
            )
        ).scalar_one_or_none()
        if existing:
            existing.cutoff_yield_pct  = row["cutoff_yield_pct"]
            existing.snapshot_date     = row["snapshot_date"]
            existing.ingested_utc      = now
        else:
            session.add(PrimaryYieldSnapshot(
                snapshot_date      = row["snapshot_date"],
                auction_date       = row["auction_date"],
                security_type      = row["security_type"],
                tenor_label        = row["tenor_label"],
                tenor_years        = row["tenor_years"],
                cutoff_yield_pct   = row["cutoff_yield_pct"],
                source             = "manual_entry",
                ingested_utc       = now,
            ))
        saved += 1
    session.commit()
    session.close()
    st.cache_data.clear()
    return saved


_TENOR_DEF = [
    ("T_BILL", "91D",  91/365.25),
    ("T_BILL", "182D", 182/365.25),
    ("T_BILL", "364D", 364/365.25),
    ("T_BOND", "2Y",   2.0),
    ("T_BOND", "5Y",   5.0),
    ("T_BOND", "10Y",  10.0),
    ("T_BOND", "15Y",  15.0),
    ("T_BOND", "20Y",  20.0),
    ("FRTB",   "3Y_FRTB", 3.0),
]


def _render_primary_yield_entry(today: datetime.date):
    """Show a manual entry form for BB Treasury primary yields."""
    st.info(
        "BB Treasury page uses browser-level bot protection — automated fetch is blocked. "
        "Enter the latest cutoff yields manually from "
        "[bb.org.bd/monetaryactivity/treasury](https://www.bb.org.bd/en/index.php/monetaryactivity/treasury)."
    )
    with st.expander("➕ Enter Primary Market Yields", expanded=True):
        auction_date = st.date_input(
            "Auction Date (from BB treasury page)",
            value=today,
            key="pye_date",
        )
        st.markdown("Enter **Weighted Average / Cutoff Yield (%)** for each tenor — leave blank to skip:")
        cols_per_row = 3
        tenors = _TENOR_DEF
        inputs: dict = {}
        for i in range(0, len(tenors), cols_per_row):
            cols = st.columns(cols_per_row)
            for j, (stype, label, _) in enumerate(tenors[i:i+cols_per_row]):
                with cols[j]:
                    val = st.text_input(
                        f"{label} ({stype})",
                        value="",
                        placeholder="e.g. 11.50",
                        key=f"pye_{label}",
                    )
                    inputs[label] = (stype, val.strip())

        if st.button("💾 Save Yields", key="pye_save"):
            rows = []
            for label, (stype, raw) in inputs.items():
                if not raw:
                    continue
                try:
                    yld = float(raw.replace(",", ""))
                except ValueError:
                    st.error(f"Invalid value for {label}: '{raw}'")
                    return
                tenor_years = next((t for st_, l, t in tenors if l == label), None)
                rows.append({
                    "snapshot_date":    today,
                    "auction_date":     auction_date,
                    "security_type":    stype,
                    "tenor_label":      label,
                    "tenor_years":      tenor_years,
                    "cutoff_yield_pct": yld,
                })
            if not rows:
                st.warning("No yields entered.")
                return
            saved = _save_primary_yields(rows)
            st.success(f"Saved {saved} yield point(s). The chart will update now.")
            st.rerun()


_TENOR_ORDER = ["14D","91D","182D","364D","2Y","3Y_FRTB","5Y","10Y","15Y","20Y"]
_TENOR_COLOURS = {
    "14D":    "#a3e635",
    "91D":    "#0d9488",
    "182D":   "#0891b2",
    "364D":   "#2563eb",
    "2Y":     "#7c3aed",
    "3Y_FRTB":"#d1780f",
    "5Y":     "#1f6feb",
    "10Y":    "#db2777",
    "15Y":    "#dc2626",
    "20Y":    "#7f1d1d",
}


def _render_yield_trend(today: datetime.date):
    """
    Historical primary market yield trend.
    Data: official BB Treasury snapshots + yields computed from GSOM issue_price / coupon_rate.
    """
    phist = load_primary_yield_history()

    if phist.empty:
        st.info("No yield history yet. Run **Refresh Data** to populate.")
        return

    st.caption(
        f"**{len(phist):,} auction data points** across {phist['Tenor'].nunique()} tenors — "
        f"official BB Treasury cutoff yields  |  "
        f"Date range: {pd.to_datetime(phist['Auction Date']).min().strftime('%b %Y')} → "
        f"{pd.to_datetime(phist['Auction Date']).max().strftime('%b %Y')}"
    )

    # ── Controls ──────────────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 1])
    with ctrl1:
        avail = [t for t in _TENOR_ORDER if t in phist["Tenor"].values]
        sel_tenors = st.multiselect("Instrument / Tenor", avail, default=avail, key="yt_tenors")
    with ctrl2:
        type_opts = sorted(phist["Type"].unique())
        sel_types = st.multiselect("Type", type_opts, default=type_opts, key="yt_types")
    with ctrl3:
        n_auctions = st.selectbox("Show last N auctions per tenor", [6, 10, 20, 50, "All"], index=0, key="yt_n")

    # Filter
    sel_tenors = sel_tenors or avail
    sel_types  = sel_types or type_opts
    df = phist[phist["Tenor"].isin(sel_tenors) & phist["Type"].isin(sel_types)].copy()

    # Keep last N per tenor
    if n_auctions != "All":
        df = (
            df.sort_values("Auction Date")
              .groupby("Tenor", group_keys=False)
              .tail(int(n_auctions))
        )

    df = df.sort_values(["Tenor", "Auction Date"]).reset_index(drop=True)

    # ── Chart ─────────────────────────────────────────────────────────────────
    fig = go.Figure()

    for tenor in sel_tenors:
        sub = df[df["Tenor"] == tenor]
        if sub.empty:
            continue
        col = _TENOR_COLOURS.get(tenor, "#888")

        fig.add_trace(go.Scatter(
            x=pd.to_datetime(sub["Auction Date"]),
            y=sub["Yield (%)"],
            mode="lines+markers+text",
            name=tenor,
            line=dict(color=col, width=2),
            marker=dict(size=10, color=col, symbol="diamond",
                        line=dict(color="white", width=1.5)),
            text=sub["Yield (%)"].map(lambda v: f"{v:.2f}%"),
            textposition="top center",
            textfont=dict(size=10, color=col),
            customdata=sub[["Type","Offered (cr)","Accepted (cr)"]].values,
            hovertemplate=(
                f"<b>{tenor}</b><br>"
                "Auction Date: %{{x|%d-%b-%Y}}<br>"
                "Cutoff Yield: <b>%{{y:.4f}}%</b><br>"
                "Offered: %{{customdata[1]}} cr | Accepted: %{{customdata[2]}} cr"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        xaxis=dict(title="Auction / Issue Date", tickformat="%b %Y",
                   showgrid=True, gridcolor="#f0f0f0"),
        yaxis=dict(title="Yield (%)", tickformat=".2f",
                   showgrid=True, gridcolor="#f0f0f0"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, title="Tenor"),
        height=520,
        plot_bgcolor="#fff",
        paper_bgcolor="#fff",
        margin=dict(t=50, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Data table (recent auctions) ──────────────────────────────────────────
    with st.expander("📋 Auction data table", expanded=False):
        disp = df.copy()
        disp["Auction Date"] = pd.to_datetime(disp["Auction Date"]).dt.strftime("%d-%b-%Y")
        disp["Yield (%)"]    = disp["Yield (%)"].map(lambda v: f"{v:.4f}%")
        st.dataframe(
            disp[["Type","Tenor","Auction Date","Yield (%)","Offered (cr)","Accepted (cr)","Source"]]
            .sort_values(["Tenor","Auction Date"], ascending=[True, False]),
            use_container_width=True, hide_index=True, height=320,
        )
        st.download_button(
            "⬇ Export CSV",
            df.to_csv(index=False).encode(),
            f"yield-trend-{today:%Y%m%d}.csv", "text/csv",
            key="dl_trend",
        )




_OMO_COLOURS = {
    "CB_REPO": "#1f6feb",
    "SLF":     "#0d9488",
    "IBLF":    "#7c3aed",
    "AR":      "#d1780f",
    "SDF":     "#dc2626",
}
_OMO_LABELS  = {"CB_REPO":"CB Repo","SLF":"SLF","IBLF":"IBLF","AR":"AR","SDF":"SDF"}
_INSTR_ORDER = ["CB_REPO","SLF","IBLF","AR","SDF"]


def _build_outstanding_series(txn_df: pd.DataFrame, date_range: pd.DatetimeIndex) -> pd.DataFrame:
    """
    For each date in date_range, compute outstanding per instrument.
    outstanding[date][instr] = sum(accepted where txn_date <= date < maturity_date)
    """
    txn_df = txn_df.copy()
    txn_df["Transaction Date"] = pd.to_datetime(txn_df["Transaction Date"])
    txn_df["Maturity Date"]    = pd.to_datetime(txn_df["Maturity Date"])
    records = []
    for d in date_range:
        active = txn_df[
            (txn_df["Transaction Date"] <= d) &
            (txn_df["Maturity Date"]    >  d)
        ]
        row = {"Date": d}
        for instr in _INSTR_ORDER:
            row[instr] = active[active["Instrument"] == instr]["Amount (Cr)"].sum()
        row["Total Injection"]  = sum(row[i] for i in ["CB_REPO","SLF","IBLF","AR"])
        row["Total Absorption"] = row["SDF"]
        row["Net"]              = row["Total Injection"] - row["Total Absorption"]
        records.append(row)
    return pd.DataFrame(records)


def _render_omo_tab(today: datetime.date):
    st.header("Open Market Operations (OMO) — Outstanding Liquidity")

    txn_df = load_omo_transactions()

    if txn_df.empty:
        st.info(
            "No OMO data yet. Click **📥 Fetch OMO Data** in the sidebar — "
            "Chrome will open, download the last 20 BB OMO press releases, and parse every transaction."
        )
        st.caption("Source: BB Press Releases — PDFs titled 'Open Market Operations'")
        return

    txn_df["Transaction Date"] = pd.to_datetime(txn_df["Transaction Date"])
    txn_df["Maturity Date"]    = pd.to_datetime(txn_df["Maturity Date"])
    min_date = txn_df["Transaction Date"].min()
    max_date = txn_df["Transaction Date"].max()

    st.caption(
        f"**{len(txn_df):,} transactions** from {min_date.strftime('%d %b %Y')} → "
        f"{max_date.strftime('%d %b %Y')}  |  "
        f"{txn_df['Transaction Date'].nunique()} OMO days"
    )

    display_start = today - datetime.timedelta(days=st.session_state.get("sb_omo_days", 28))
    date_range = pd.date_range(
        start=max(min_date.date(), display_start),
        end=today + datetime.timedelta(days=14),
        freq="D",
    )
    outstanding = _build_outstanding_series(txn_df, date_range)

    # ── Today's KPIs ──────────────────────────────────────────────────────────
    today_row = outstanding[outstanding["Date"].dt.date == today]
    tr = today_row.iloc[0] if not today_row.empty else outstanding.iloc[-1]

    st.subheader(f"Outstanding as of {pd.Timestamp(tr['Date']).strftime('%d %b %Y')}")
    k1, k2, k3 = st.columns(3)
    k1.metric("Total Injection (Cr)", f"{tr['Total Injection']:,.0f}",
              help="CB Repo + SLF + IBLF + AR")
    k2.metric("Total Absorption (Cr)", f"{tr['Total Absorption']:,.0f}",
              help="SDF — banks depositing at BB")
    net_val = tr["Net"]
    k3.metric("Net Injection (Cr)", f"{net_val:,.0f}",
              delta="Injecting" if net_val > 0 else "Absorbing",
              delta_color="normal" if net_val > 0 else "inverse")

    inst_cols = st.columns(5)
    for col, instr in zip(inst_cols, _INSTR_ORDER):
        val = tr[instr]
        col.metric(_OMO_LABELS[instr], f"{val:,.0f} Cr" if val > 0 else "—")

    # ── Outstanding timeline ──────────────────────────────────────────────────
    st.subheader("Outstanding Liquidity Timeline")
    st.caption(
        "Cumulative outstanding = all not-yet-matured transactions per instrument per day. "
        "Shaded area = future (upcoming maturities)."
    )

    c1, c2 = st.columns([3, 1])
    with c1:
        avail = [i for i in _INSTR_ORDER if outstanding[i].sum() > 0]
        sel   = st.multiselect("Instruments", avail, default=avail, key="omo_instrs")
    with c2:
        mode = st.radio("Style", ["Stacked","Lines"], key="omo_mode", horizontal=True)

    sel = sel or avail
    fig = go.Figure()

    if mode == "Stacked":
        for instr in sel:
            colour = _OMO_COLOURS.get(instr, "#888")
            direction = "ABSORPTION" if instr == "SDF" else "INJECTION"
            fig.add_trace(go.Bar(
                x=outstanding["Date"], y=outstanding[instr],
                name=_OMO_LABELS[instr], marker_color=colour,
                hovertemplate=(
                    f"<b>{_OMO_LABELS[instr]}</b> ({direction})<br>"
                    "Date: %{x|%d-%b-%Y}<br>Outstanding: <b>%{y:,.0f} Cr</b><extra></extra>"
                ),
            ))
        fig.update_layout(barmode="stack")
    else:
        for instr in sel:
            colour = _OMO_COLOURS.get(instr, "#888")
            fig.add_trace(go.Scatter(
                x=outstanding["Date"], y=outstanding[instr],
                mode="lines+markers", name=_OMO_LABELS[instr],
                line=dict(color=colour, width=2), marker=dict(size=5, color=colour),
                hovertemplate=(
                    f"<b>{_OMO_LABELS[instr]}</b><br>"
                    "Date: %{x|%d-%b-%Y}<br>Outstanding: <b>%{y:,.0f} Cr</b><extra></extra>"
                ),
            ))

    today_str = today.isoformat()
    fig.add_vline(x=today_str, line_dash="dot", line_color="#555", line_width=1.5)
    fig.add_vrect(x0=today_str, x1=outstanding["Date"].max().isoformat(),
                  fillcolor="rgba(180,180,180,0.1)", layer="below", line_width=0)
    fig.update_layout(
        xaxis=dict(title="Date", tickformat="%d-%b"),
        yaxis=dict(title="BDT Crore", tickformat=",.0f"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=440, plot_bgcolor="#fff", paper_bgcolor="#fff",
        margin=dict(t=50, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Net injection chart ───────────────────────────────────────────────────
    st.subheader("Net Injection vs Absorption")
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=outstanding["Date"], y=outstanding["Total Injection"],
                          name="Injection", marker_color="#1a7f37",
                          hovertemplate="Injection: <b>%{y:,.0f} Cr</b><extra></extra>"))
    fig2.add_trace(go.Bar(x=outstanding["Date"], y=-outstanding["Total Absorption"],
                          name="Absorption (SDF)", marker_color="#cf222e",
                          hovertemplate="Absorption: <b>%{y:,.0f} Cr</b><extra></extra>"))
    fig2.add_trace(go.Scatter(x=outstanding["Date"], y=outstanding["Net"],
                              name="Net Injection", mode="lines",
                              line=dict(color="#d1780f", width=2.5, dash="dot"),
                              yaxis="y2",
                              hovertemplate="Net: <b>%{y:,.0f} Cr</b><extra></extra>"))
    fig2.add_vline(x=today.isoformat(), line_dash="dot", line_color="#555", line_width=1.5)
    fig2.update_layout(
        barmode="relative",
        xaxis=dict(title="Date", tickformat="%d-%b"),
        yaxis=dict(title="BDT Crore"),
        yaxis2=dict(title="Net (Cr)", overlaying="y", side="right"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=360, plot_bgcolor="#fff", paper_bgcolor="#fff",
    )
    st.plotly_chart(fig2, use_container_width=True)

    # ── Per-instrument breakdown with days to maturity ───────────────────────
    st.subheader("Outstanding Securities — Per Instrument Breakdown")
    st.caption("Every active transaction: what's in the market right now, when it matures, and how many days left.")

    INSTR_LABELS = {
        "CB_REPO": "Central Bank Repo (CB Repo)",
        "SLF":     "Standing Lending Facility (SLF)",
        "IBLF":    "Islami Banks Liquidity Facility (IBLF)",
        "AR":      "Assured Repo (AR)",
        "SDF":     "Standing Deposit Facility (SDF)",
    }
    INSTR_DIR = {"CB_REPO":"INJECTION","SLF":"INJECTION","IBLF":"INJECTION",
                 "AR":"INJECTION","SDF":"ABSORPTION"}

    active = txn_df[
        (txn_df["Transaction Date"].dt.date <= today) &
        (txn_df["Maturity Date"].dt.date    >  today)
    ].copy()
    active["Days Left"] = (active["Maturity Date"].dt.date - today).apply(lambda d: d.days)

    for instr in ["CB_REPO","SLF","IBLF","AR","SDF"]:
        sub = active[active["Instrument"] == instr].sort_values("Maturity Date")
        if sub.empty:
            continue

        total    = sub["Amount (Cr)"].sum()
        n_txns   = len(sub)
        dir_lbl  = "Injection" if INSTR_DIR[instr] == "INJECTION" else "Absorption"
        dir_col  = "normal" if INSTR_DIR[instr] == "INJECTION" else "inverse"
        min_days = sub["Days Left"].min()
        urgency  = "🔴" if min_days <= 3 else ("🟡" if min_days <= 7 else "🟢")

        with st.expander(
            f"{urgency} **{INSTR_LABELS[instr]}** — {total:,.2f} Cr outstanding  "
            f"({n_txns} {'transaction' if n_txns==1 else 'transactions'}, "
            f"nearest maturity in {min_days}d)",
            expanded=(min_days <= 7),
        ):
            col_a, col_b = st.columns([3, 1])
            with col_a:
                disp = sub.copy()
                disp["Transaction Date"] = disp["Transaction Date"].dt.strftime("%d-%b-%Y")
                disp["Maturity Date"]    = disp["Maturity Date"].dt.strftime("%d-%b-%Y")
                disp["Amount (Cr)"]      = disp["Amount (Cr)"].map(lambda v: f"{v:,.2f}")
                disp["Rate (%)"]         = disp["Rate (%)"].apply(
                    lambda v: f"{v:.2f}%" if pd.notna(v) else "—"
                )
                disp["Days Left"]        = disp["Days Left"].map(lambda d: f"{d}d")
                st.dataframe(
                    disp[["Transaction Date","Tenor","Amount (Cr)","Rate (%)","Maturity Date","Days Left"]],
                    use_container_width=True, hide_index=True,
                )
            with col_b:
                st.metric("Total Outstanding", f"{total:,.0f} Cr")
                st.metric("Transactions", n_txns)
                st.metric("Nearest maturity", f"{min_days} days")
                st.metric("Direction", dir_lbl)

    # ── Maturity schedule ─────────────────────────────────────────────────────
    st.subheader("Maturity Schedule (next 60 days)")
    st.caption("Liquidity leaving the market as injections expire.")

    end_60 = today + datetime.timedelta(days=60)
    sched = active[active["Maturity Date"].dt.date <= end_60].copy()
    sched["Days Left"] = (sched["Maturity Date"].dt.date - today).apply(lambda d: d.days)

    if sched.empty:
        st.caption("No maturities in next 60 days.")
    else:
        # Group by date
        by_date = (
            sched.groupby(["Maturity Date","Instrument","Direction"])["Amount (Cr)"]
            .sum()
            .reset_index()
            .sort_values("Maturity Date")
        )
        by_date["Days Left"] = (
            pd.to_datetime(by_date["Maturity Date"]).dt.date - today
        ).apply(lambda d: d.days)
        by_date["Impact"] = by_date["Direction"].map({
            "INJECTION":  "liquidity leaves market",
            "ABSORPTION": "liquidity returns to market",
        })
        by_date["Maturity Date"] = pd.to_datetime(by_date["Maturity Date"]).dt.strftime("%d-%b-%Y")
        by_date["Amount (Cr)"]   = by_date["Amount (Cr)"].map(lambda v: f"{v:,.2f}")
        by_date["Days Left"]     = by_date["Days Left"].map(lambda d: f"{d}d")
        by_date["Flag"]          = by_date["Days Left"].apply(
            lambda d: "🔴 DUE SOON" if int(d[:-1]) <= 3 else ("🟡" if int(d[:-1]) <= 7 else "")
        )
        st.dataframe(
            by_date[["Maturity Date","Days Left","Instrument","Amount (Cr)","Direction","Impact","Flag"]],
            use_container_width=True, hide_index=True, height=420,
        )

        # Maturity bar chart
        chart_df = (
            sched.groupby(["Maturity Date","Instrument"])["Amount (Cr)"]
            .sum()
            .reset_index()
        )
        fig_mat = go.Figure()
        for instr in ["CB_REPO","SLF","IBLF","AR","SDF"]:
            sub = chart_df[chart_df["Instrument"] == instr]
            if sub.empty:
                continue
            fig_mat.add_trace(go.Bar(
                x=sub["Maturity Date"],
                y=sub["Amount (Cr)"],
                name=instr,
                marker_color=_OMO_COLOURS.get(instr, "#888"),
                hovertemplate=f"<b>{instr}</b><br>Date: %{{x}}<br>Amount: <b>%{{y:,.0f}} Cr</b><extra></extra>",
            ))
        fig_mat.update_layout(
            barmode="stack",
            xaxis=dict(title="Maturity Date", tickformat="%d-%b"),
            yaxis=dict(title="BDT Crore", tickformat=",.0f"),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            height=320, plot_bgcolor="#fff", paper_bgcolor="#fff",
        )
        st.plotly_chart(fig_mat, use_container_width=True)

    # ── Raw transactions ──────────────────────────────────────────────────────
    # ── Daily BB schedule (raw PDF data) ─────────────────────────────────────
    st.subheader("Daily BB OMO Schedule — Raw Data")
    st.caption("Exactly as parsed from BB press release PDFs. New injections/absorptions per day.")

    all_dates = sorted(txn_df["Transaction Date"].dt.date.unique(), reverse=True)
    sel_date  = st.selectbox(
        "Select date", all_dates,
        format_func=lambda d: f"{d.strftime('%d %b %Y')} ({d.strftime('%a')})",
        key="omo_day_sel",
    )

    day_df = txn_df[txn_df["Transaction Date"].dt.date == sel_date].copy()
    inj_df = day_df[day_df["Direction"] == "INJECTION"].sort_values(["Instrument","Tenor"])
    abs_df = day_df[day_df["Direction"] == "ABSORPTION"].sort_values(["Instrument","Tenor"])
    total_inj = inj_df["Amount (Cr)"].sum()
    total_abs = abs_df["Amount (Cr)"].sum()
    net       = total_inj - total_abs

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Injection",  f"{total_inj:,.2f} Cr")
    m2.metric("Total Absorption", f"{total_abs:,.2f} Cr")
    m3.metric("Net",              f"{net:+,.2f} Cr",
              delta="Injecting" if net > 0 else "Absorbing",
              delta_color="normal" if net > 0 else "inverse")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Injection (+)**")
        if inj_df.empty:
            st.caption("No injection on this day.")
        else:
            disp_inj = inj_df[["Instrument","Tenor","Amount (Cr)","Rate (%)"]].copy()
            disp_inj["Amount (Cr)"] = disp_inj["Amount (Cr)"].map(lambda v: f"{v:,.2f}")
            disp_inj["Rate (%)"]    = disp_inj["Rate (%)"].apply(
                lambda v: f"{v:.2f}%" if pd.notna(v) else "—"
            )
            st.dataframe(disp_inj, use_container_width=True, hide_index=True)
            st.markdown(f"**Total: {total_inj:,.2f} Cr**")
    with c2:
        st.markdown("**Absorption (−)**")
        if abs_df.empty:
            st.caption("No absorption on this day.")
        else:
            disp_abs = abs_df[["Instrument","Tenor","Amount (Cr)","Rate (%)"]].copy()
            disp_abs["Amount (Cr)"] = disp_abs["Amount (Cr)"].map(lambda v: f"{v:,.2f}")
            disp_abs["Rate (%)"]    = disp_abs["Rate (%)"].apply(
                lambda v: f"{v:.2f}%" if pd.notna(v) else "—"
            )
            st.dataframe(disp_abs, use_container_width=True, hide_index=True)
            st.markdown(f"**Total: {total_abs:,.2f} Cr**")

    with st.expander("📋 All transactions (full history)", expanded=False):
        disp = txn_df.copy()
        disp["Transaction Date"] = disp["Transaction Date"].dt.strftime("%d-%b-%Y")
        disp["Maturity Date"]    = disp["Maturity Date"].dt.strftime("%d-%b-%Y")
        disp["Amount (Cr)"]      = disp["Amount (Cr)"].map(lambda v: f"{v:,.2f}")
        st.dataframe(
            disp[["Transaction Date","Maturity Date","Instrument","Tenor","Amount (Cr)","Rate (%)","Direction"]]
            .sort_values("Transaction Date", ascending=False),
            use_container_width=True, hide_index=True, height=380,
        )
        st.download_button(
            "⬇ Export CSV", txn_df.to_csv(index=False).encode(),
            f"omo-transactions-{today:%Y%m%d}.csv", "text/csv", key="dl_omo",
        )


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — called on every page load/reload
# ══════════════════════════════════════════════════════════════════════════════

def render():
    today = datetime.date.today()

    st.markdown("""
<style>
.stDataFrame { font-size: 13px; }
div[data-testid="metric-container"] { background:#f0f4fa; border-radius:8px; padding:8px; }
</style>
""", unsafe_allow_html=True)

    # ── GLOBAL FILTER SIDEBAR ─────────────────────────────────────────────────
    st.sidebar.title("🏦 BD Money Market")
    st.sidebar.markdown("---")

    db_range = _get_data_date_range()

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
    n_auctions = st.sidebar.selectbox(
        "Last N auctions per tenor", [3, 6, 8, 12], index=1, key="sb_months",
        help="Fetches enough months to cover this many recent auctions for each instrument (bonds auction ~monthly)"
    )
    if st.sidebar.button("📈 Fetch Yield History (BB Treasury)", use_container_width=True):
        with st.spinner(f"Fetching last {n_auctions} auctions from BB Treasury — opening Chrome…"):
            st.cache_data.clear()
            result = run_primary_yield_history(months_back=n_auctions)
            if result["errors"]:
                st.sidebar.error(f"Errors: {result['errors']}")
            else:
                st.sidebar.success(f"✓ {result['rows']} yield points stored ({n_auctions} months covered)")
            st.rerun()

    st.sidebar.markdown("---")
    omo_days = st.sidebar.selectbox(
        "OMO lookback (days)", [7, 14, 28, 60], index=2, key="sb_omo_days",
        help="How many days back to fetch OMO press releases",
    )
    if st.sidebar.button("📥 Fetch OMO Data (BB Press Release)", use_container_width=True):
        with st.spinner(f"Fetching last {omo_days} days of OMO PDFs — opening Chrome…"):
            st.cache_data.clear()
            result = run_omo_fetch(days_back=omo_days, max_files=15)
            if result["errors"]:
                st.sidebar.error(f"Errors: {result['errors']}")
            else:
                st.sidebar.success(f"✓ {result['rows']} OMO rows stored")
            st.rerun()

    st.sidebar.markdown("---")
    if not db_range:
        st.sidebar.warning("No data yet. Click Refresh Data to run the pipeline.")
    else:
        st.sidebar.caption(f"Data: {db_min} → {db_max}")

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📅 Daily Summary",
        "📋 Event Table",
        "🔒 Securities",
        "🔍 Date Drilldown",
        "📈 Yield Curve",
        "💧 OMO",
        "⚠️ Data Quality",
    ])

    # ════════════════════════════════════════════════════════════════════════
    # TAB 1 — Daily Summary
    # ════════════════════════════════════════════════════════════════════════
    with tab1:
        st.header("Daily Cash Flow Summary")

        if not db_range:
            st.info("No data loaded yet. Click **Refresh Data** in the sidebar.")
        else:
            df = load_daily_flows(date_from, date_to)

            if df.empty:
                st.warning("No daily flow data in selected range. Try widening the date filter.")
            else:
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

    # ════════════════════════════════════════════════════════════════════════
    # TAB 2 — Event Table
    # ════════════════════════════════════════════════════════════════════════
    with tab2:
        st.header("All Cash Flow Events")
        edf = load_events(date_from, date_to, tuple(event_types), tuple(instruments))

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

    # ════════════════════════════════════════════════════════════════════════
    # TAB 3 — Securities
    # ════════════════════════════════════════════════════════════════════════
    with tab3:
        st.header("Security Master")
        sdf = load_securities(tuple(instruments))

        if sdf.empty:
            st.info("No securities loaded. Run the pipeline first.")
        else:
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

    # ════════════════════════════════════════════════════════════════════════
    # TAB 4 — Date Drilldown
    # ════════════════════════════════════════════════════════════════════════
    with tab4:
        st.header("Date Drilldown")

        if not db_range:
            st.info("No data loaded yet.")
        else:
            drill_min = db_range[0]
            drill_max = db_range[1]
            drill_default = safe_date_input_value(today, drill_min, drill_max)

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
            bill_out = sum(
                (a.accepted_amount_bdt_mill or a.offered_amount_bdt_mill or 0)
                for a in aucs if a.security_type == "T_BILL"
            )
            bond_out = sum(
                (a.accepted_amount_bdt_mill or a.offered_amount_bdt_mill or 0)
                for a in aucs if a.security_type in ("T_BOND", "FRTB")
            )
            net = auc_total - mat_total - coup_total

            s1,s2,s3,s4,s5,s6 = st.columns(6)
            s1.metric("Maturity Inflow",    f"৳{mat_total:,.0f}")
            s2.metric("Coupon Inflow",      f"৳{coup_total:,.0f}")
            s3.metric("Total Inflow",       f"৳{mat_total+coup_total:,.0f}")
            s4.metric("T-Bill Outflow",     f"৳{bill_out:,.0f}")
            s5.metric("Bond/FRTB Outflow",  f"৳{bond_out:,.0f}")
            s6.metric("Net Borrowing",      f"৳{net:,.0f}",
                      delta_color="inverse" if net > 0 else "normal")

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

    # ════════════════════════════════════════════════════════════════════════
    # TAB 5 — Yield Curve
    # ════════════════════════════════════════════════════════════════════════
    with tab5:
        st.header("BB Yield Curve")

        ydf, snap_date = load_yield_curve()
        pdf = load_primary_yield_curve()

        has_secondary = not ydf.empty
        has_primary   = not pdf.empty

        if not has_secondary and not has_primary:
            st.info("No yield data yet. Click **Refresh Data** in the sidebar to run the pipeline.")
        else:
            # ── View selector ─────────────────────────────────────────────────
            view = st.radio(
                "View",
                ["Both", "Secondary Market (GSOM)", "Primary Market (BB Treasury)"],
                horizontal=True,
                key="yc_view",
            )

            show_secondary = view in ("Both", "Secondary Market (GSOM)")
            show_primary   = view in ("Both", "Primary Market (BB Treasury)")

            # ── Source captions ───────────────────────────────────────────────
            captions = []
            if show_secondary and has_secondary:
                captions.append(f"Secondary: BB GSOM MTM — settlement **{snap_date}**")
            if show_primary and has_primary:
                latest_auc = pdf["Auction Date"].max()
                captions.append(f"Primary: BB Treasury cutoff rates — latest auction **{latest_auc}**")
            if captions:
                st.caption("  |  ".join(captions))

            # ── Instrument filter ─────────────────────────────────────────────
            all_types = set()
            if show_secondary and has_secondary:
                all_types.update(ydf["Type"].unique())
            if show_primary and has_primary:
                all_types.update(pdf["Type"].unique())
            type_opts = sorted(all_types)
            sel_types = st.multiselect("Instrument", type_opts, default=type_opts, key="yc_types")

            colour_map = {"T_BILL": "#0d9488", "T_BOND": "#1f6feb", "FRTB": "#d1780f"}

            # ── KPI strip ─────────────────────────────────────────────────────
            kpi_df = ydf if (show_secondary and has_secondary) else pdf
            kpi_col = "Yield (%)"
            kpi_type_col = "Type"
            if not kpi_df.empty:
                kb = kpi_df[kpi_df[kpi_type_col] == "T_BILL"]
                kn = kpi_df[kpi_df[kpi_type_col] == "T_BOND"]
                c1, c2, c3, c4 = st.columns(4)
                if not kb.empty:
                    c1.metric("T-Bill short end (%)", f"{kb[kpi_col].min():.2f}")
                    c2.metric("T-Bill long end (%)",  f"{kb[kpi_col].max():.2f}")
                if not kn.empty:
                    c3.metric("T-Bond short end (%)", f"{kn[kpi_col].min():.2f}")
                    c4.metric("T-Bond long end (%)",  f"{kn[kpi_col].max():.2f}")

            # ── Combined chart ────────────────────────────────────────────────
            fig = go.Figure()

            if show_secondary and has_secondary:
                sec_filt = ydf[ydf["Type"].isin(sel_types)] if sel_types else ydf
                for stype in (sel_types or type_opts):
                    sub = sec_filt[sec_filt["Type"] == stype].sort_values("Remaining Maturity (Yrs)")
                    if sub.empty:
                        continue
                    col = colour_map.get(stype, "#888")
                    fig.add_trace(go.Scatter(
                        x=sub["Remaining Maturity (Yrs)"],
                        y=sub["Yield (%)"],
                        mode="lines+markers",
                        name=f"{stype} (Secondary)",
                        legendgroup=stype,
                        line=dict(color=col, width=2, dash="solid"),
                        marker=dict(size=5, color=col),
                        customdata=sub[["ISIN", "Security Name", "Maturity Date", "Outstanding (mn)"]].values,
                        hovertemplate=(
                            "<b>%{customdata[1]}</b> [Secondary]<br>"
                            "ISIN: %{customdata[0]}<br>"
                            "Maturity: %{customdata[2]}<br>"
                            "Rem. Maturity: %{x:.2f} yrs<br>"
                            "Yield: <b>%{y:.4f}%</b><br>"
                            "Outstanding: %{customdata[3]:,.0f} mn<extra></extra>"
                        ),
                    ))

            if show_primary and has_primary:
                pri_filt = pdf[pdf["Type"].isin(sel_types)] if sel_types else pdf
                for stype in (sel_types or sorted(pdf["Type"].unique())):
                    sub = pri_filt[pri_filt["Type"] == stype].sort_values("Tenor (Yrs)")
                    if sub.empty:
                        continue
                    col = colour_map.get(stype, "#888")
                    fig.add_trace(go.Scatter(
                        x=sub["Tenor (Yrs)"],
                        y=sub["Yield (%)"],
                        mode="lines+markers+text",
                        name=f"{stype} (Primary)",
                        legendgroup=stype,
                        line=dict(color=col, width=2, dash="dash"),
                        marker=dict(size=10, symbol="diamond", color=col),
                        text=sub["Yield (%)"].map(lambda v: f"{v:.2f}%"),
                        textposition="top center",
                        textfont=dict(size=11, color=col),
                        customdata=sub[["Tenor", "Auction Date", "Offered (crore)", "Accepted (crore)"]].values,
                        hovertemplate=(
                            "<b>%{customdata[0]}</b> [Primary]<br>"
                            "Auction Date: %{customdata[1]}<br>"
                            "Tenor: %{x:.2f} yrs<br>"
                            "Cutoff Yield: <b>%{y:.4f}%</b><br>"
                            "Offered: %{customdata[2]} cr | Accepted: %{customdata[3]} cr<extra></extra>"
                        ),
                    ))

            fig.update_layout(
                xaxis=dict(title="Maturity / Tenor (Years)", tickformat=".1f"),
                yaxis=dict(title="Yield (%)"),
                hovermode="closest",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                height=500,
                plot_bgcolor="#fff",
                paper_bgcolor="#fff",
            )
            st.plotly_chart(fig, use_container_width=True)

            # ── Data tables ───────────────────────────────────────────────────
            t_sec, t_pri = st.tabs(["Secondary Market Data", "Primary Market Data"])

            with t_sec:
                if has_secondary:
                    disp = ydf.copy()
                    disp["Maturity Date"] = pd.to_datetime(disp["Maturity Date"]).dt.strftime("%d-%b-%Y")
                    disp["Yield (%)"] = disp["Yield (%)"].map(lambda v: f"{v:.4f}")
                    disp["Remaining Maturity (Yrs)"] = disp["Remaining Maturity (Yrs)"].map(lambda v: f"{v:.2f}")
                    st.dataframe(
                        disp[["Type","Security Name","ISIN","Maturity Date",
                              "Remaining Maturity (Yrs)","Yield (%)","Outstanding (mn)"]],
                        use_container_width=True, hide_index=True, height=340,
                    )
                    st.download_button(
                        "⬇ Export Secondary CSV",
                        ydf.to_csv(index=False).encode(),
                        f"secondary-yield-{snap_date}.csv", "text/csv",
                        key="dl_sec",
                    )
                else:
                    st.info("No secondary yield data. Run Refresh Data.")

            with t_pri:
                if has_primary:
                    disp2 = pdf.copy()
                    disp2["Auction Date"] = pd.to_datetime(disp2["Auction Date"]).dt.strftime("%d-%b-%Y")
                    disp2["Yield (%)"] = disp2["Yield (%)"].map(lambda v: f"{v:.4f}")
                    disp2["Tenor (Yrs)"] = disp2["Tenor (Yrs)"].map(lambda v: f"{v:.3f}")
                    st.dataframe(
                        disp2[["Type","Tenor","Tenor (Yrs)","Auction Date",
                               "Yield (%)","Offered (crore)","Accepted (crore)","Source"]],
                        use_container_width=True, hide_index=True, height=280,
                    )
                    st.download_button(
                        "⬇ Export Primary CSV",
                        pdf.to_csv(index=False).encode(),
                        f"primary-yield-{today:%Y%m%d}.csv", "text/csv",
                        key="dl_pri",
                    )
                with st.expander("➕ Manually override / add yields", expanded=not has_primary):
                    _render_primary_yield_entry(today)

        # ── Yield Trend section ───────────────────────────────────────────────
        st.divider()
        st.subheader("📊 Yield Trend — Primary Market Auction History")

        _render_yield_trend(today)

    # ════════════════════════════════════════════════════════════════════════
    # TAB 6 — OMO
    # ════════════════════════════════════════════════════════════════════════
    with tab6:
        _render_omo_tab(today)

    # ════════════════════════════════════════════════════════════════════════
    # TAB 7 — Data Quality
    # ════════════════════════════════════════════════════════════════════════
    with tab7:
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

        if bad_sec:
            st.subheader("Flagged Securities")
            st.dataframe(pd.DataFrame([{
                "ISIN": s.isin, "Name": s.security_name_norm, "Flag": s.data_quality
            } for s in bad_sec]), hide_index=True)

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
