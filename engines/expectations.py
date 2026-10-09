"""What every dataset is expected to look like, in one place.

Written after eleven days of OMO data went missing while every check reported
green. The failure was not that nothing noticed — validate.py's own message
read "no OMO transaction in 10 days — publication gap this long is
implausible". The failure was that it filed that conclusion under `waiting`,
which by design never fails a build and never alerts.

Three rules come out of that, and they are why this module exists:

  1. WAITING MUST EXPIRE. "BB is a day late" and "BB has stopped publishing"
     look identical in the data and are completely different problems. A
     tolerance per cadence turns the second into a defect automatically,
     without anyone having to notice.

  2. A FETCH THAT WROTE NOTHING IS NOT A HEALTHY FETCH. Judging health by
     "did the step raise?" cannot tell "BB published nothing" from "we are
     broken" — which is exactly the pair that mattered.

  3. EVERY DATASET IS DECLARED OR DELIBERATELY EXEMPT. Nine datasets had no
     freshness rule at all, so no amount of staleness could ever have been
     reported for them. A test enforces this list against the real schema.

Thresholds are per CADENCE, not per table, so a new dataset inherits a sane
default instead of needing a judgement call nobody will revisit.
"""
import datetime
from typing import Dict, List, Optional

# ── cadences ────────────────────────────────────────────────────────────────
WORKING_DAY = "working_day"
WEEKLY = "weekly"
MONTHLY = "monthly"
EVENT = "event"          # no schedule: staleness is meaningless, gaps are not
DERIVED = "derived"      # we compute it; staleness means OUR pipeline stalled

# days of tolerance before `waiting` becomes a `defect`
# Tolerance must sit ABOVE the staleness rule that reports the gap, or `waiting`
# is unreachable and every late day is instantly a defect -- which is the
# opposite failure, and how alarms become wallpaper. The daily rules fire past
# 4-5 days, so a full week of silence on a daily series is the escalation point.
# When the staleness rule FIRES (becomes visible at all). Separate from
# TOLERANCE, which is when it escalates. OMO's hand-picked rule fired only
# after 10 DAYS -- for a series BB publishes every working day -- so the system
# was structurally blind for ten days and then called it `waiting` forever.
# A daily series must be visible within one long weekend plus a day.
STALE_AFTER: Dict[str, int] = {
    WORKING_DAY: 2,      # WORKING days — two consecutive missed publications
    WEEKLY: 10,          # calendar days
    MONTHLY: 45,
    DERIVED: 3,
    EVENT: None,
}

TOLERANCE: Dict[str, int] = {
    WORKING_DAY: 4,      # WORKING days — a full trading week of silence is ours
    WEEKLY: 14,          # calendar days: two missed cycles
    # BB's econdata publishes a month in arrears and then some: checked
    # 2026-10-10, BB's own reserves page carried nothing newer than 2026-08,
    # i.e. 70 days. Escalating at 50 would have called BB's normal cadence our
    # failure -- the false-alarm side of the same mistake, and just as corrosive.
    MONTHLY: 85,
    DERIVED: 5,          # our own pipeline, so no publication excuse
    EVENT: None,         # never stale by time alone
}


class Dataset:
    """One dataset and what we expect of it.

    `step` names the fetch step that feeds it, so a step that ran and wrote
    nothing can be tied back to the dataset it was supposed to fill.
    """

    def __init__(self, key: str, table: str, date_column: str, cadence: str,
                 label: str, source: str, step: Optional[str] = None,
                 gap_check: bool = True, tolerance_days: Optional[int] = None,
                 stale_after_days: Optional[int] = None, note: str = ""):
        self.key = key
        self.table = table
        self.date_column = date_column
        self.cadence = cadence
        self.label = label
        self.source = source
        self.step = step
        self.gap_check = gap_check
        self._tolerance = tolerance_days
        self._stale_after = stale_after_days
        self.note = note

    @property
    def stale_after_days(self) -> Optional[int]:
        """Days before the gap is reported at all."""
        if self._stale_after is not None:
            return self._stale_after
        return STALE_AFTER[self.cadence]

    @property
    def tolerance_days(self) -> Optional[int]:
        return self._tolerance if self._tolerance is not None else TOLERANCE[self.cadence]

    def age(self, latest: Optional[datetime.date], today: datetime.date,
            holidays=None) -> Optional[int]:
        """How far behind this dataset is, in the unit its cadence is published in.

        A working-day series must be measured in WORKING DAYS. Counting calendar
        days flags every daily series each weekend -- Bangladesh closes Friday
        and Saturday, so on a Saturday everything is legitimately two to three
        calendar days old. Alerting on that is how an alarm becomes wallpaper,
        which is the failure the whole waiting/defect split exists to avoid.
        """
        if latest is None:
            return None
        if self.cadence == WORKING_DAY:
            return working_days_between(latest, today, holidays)
        return (today - latest).days

    def severity_for_age(self, age: Optional[int]) -> Optional[str]:
        """None | 'waiting' | 'defect', given an age already in the right unit."""
        tol = self.tolerance_days
        if age is None or tol is None:
            return None
        if age <= 1:
            return None
        return "defect" if age > tol else "waiting"

    def __repr__(self):
        return f"<Dataset {self.key} {self.cadence}>"


DATASETS: List[Dataset] = [
    # ── BB publishes these every working day ────────────────────────────────
    Dataset("omo", "omo_transactions", "transaction_date", WORKING_DAY,
            "OMO operations", "BB press releases", step="omo",
            note="BB posts one release per working day; a multi-day gap is ours."),
    Dataset("callmoney", "call_money_rates", "trade_date", WORKING_DAY,
            "Call money", "BB call money page", step="callmoney"),
    Dataset("fxmarket", "interbank_fx", "trade_date", WORKING_DAY,
            "Interbank FX", "BB interbank FX market", step="fxmarket"),
    Dataset("repo", "interbank_repo", "trade_date", WORKING_DAY,
            "Interbank repo", "BB interbank repo", step="repo"),
    Dataset("fxrates", "fx_rates_daily", "rate_date", WORKING_DAY,
            "FX rates", "BB exchange rate page", step="fxrates",
            stale_after_days=3,
            note="Describes the PREVIOUS business day, so one day behind is normal."),
    Dataset("secondary", "mtm_snapshots", "settlement_date", WORKING_DAY,
            "Secondary market (GSOM)", "BB GSOM", step="gsom"),
    Dataset("refrate", "ref_rates", "trade_date", WORKING_DAY,
            "Reference rates (BOFR/DOMMR)", "BB reference rate", step="refrate",
            note="Six rows a day across BOFR and DOMMR — a daily series, not monthly."),

    # ── weekly-ish ──────────────────────────────────────────────────────────
    Dataset("yields", "primary_yield_snapshots", "issue_date", WEEKLY,
            "Primary auction results", "BB treasury results", step="treasury"),
    Dataset("securities", "securities", "issue_date", WEEKLY,
            "Securities master", "BB GSOM", step="gsom"),

    # ── monthly ─────────────────────────────────────────────────────────────
    Dataset("reserves", "reserves_monthly", "month", MONTHLY,
            "FX reserves", "BB econdata", step="reserves"),
    Dataset("remittance", "remittance_monthly", "month", MONTHLY,
            "Remittance", "BB econdata", step="remittance"),
    Dataset("monetary", "monetary_monthly", "month", MONTHLY,
            "Monetary aggregates", "BB econdata", step="monetary"),

    # ── event-driven: a gap is meaningless, so only completeness is checked ──
    Dataset("auctions_forward", "auction_events", "auction_date", EVENT,
            "Auction calendar", "BB auction calendar", step="auction_calendar",
            gap_check=False,
            note="Forward-looking: its 'staleness' is the horizon, checked separately."),
    Dataset("fxauction", "fx_auction_results", "auction_date", EVENT,
            "BB FX auctions", "BB FX auction results", step="fx", gap_check=False,
            note="BB intervenes irregularly; silence is normal and means nothing."),
    Dataset("policy", "policy_rate_snapshots", "last_seen_date", EVENT,
            "Policy corridor", "BB homepage", step="policy", gap_check=False,
            note="Changes only when the MPC moves."),
    Dataset("holidays", "holiday_calendar", "calendar_date", EVENT,
            "Holiday calendar", "BB gazette", step=None, gap_check=False,
            note="Annual gazette; coverage of the YEAR AHEAD is what matters, "
                 "not recency — settlement dates roll off this."),

    # ── we compute these ────────────────────────────────────────────────────
    Dataset("flows", "daily_net_flow", "flow_date", DERIVED,
            "Cash-flow ladder", "computed", step="pipeline", gap_check=False,
            note="Forward-looking; its horizon is checked, not its recency."),
    Dataset("coupons", "coupon_events", "payment_date", DERIVED,
            "Coupon schedule", "computed", step="pipeline", gap_check=False),
    Dataset("maturities", "maturity_events", "payment_date", DERIVED,
            "Maturity schedule", "computed", step="pipeline", gap_check=False),
]

BY_KEY: Dict[str, Dataset] = {d.key: d for d in DATASETS}
BY_TABLE: Dict[str, Dataset] = {d.table: d for d in DATASETS}

# Tables deliberately NOT watched, each with the reason. Anything else that
# appears in the schema fails the coverage test rather than being quietly
# unmonitored, which is how nine datasets came to have no rule at all.
NOT_WATCHED: Dict[str, str] = {
    "pipeline_runs": "our own run log — it IS the evidence, not a dataset",
    "data_issues": "the guardian's ledger; watched by the heartbeat instead",
    "data_changes": "audit trail of automatic repairs, append-only",
    "auction_forecasts": "model output, not published data",
    "backtest_metrics": "model evaluation output",
    "backtest_predictions": "model evaluation output",
    "research_diagnostics": "research scratch output",
    "policy_rate_history": "seeded history behind policy_rate_snapshots",
    "mtm_snapshots": "watched as 'secondary' above (listed for completeness)",
    "source_snapshots": "structural fingerprints; watched by the canary itself",
}


def working_days_between(a: datetime.date, b: datetime.date, holidays=None) -> int:
    """Working days in (a, b], Bangladesh weekend = Friday + Saturday."""
    holidays = holidays or set()
    n, d = 0, a + datetime.timedelta(days=1)
    while d <= b:
        if d.weekday() not in (4, 5) and d not in holidays:
            n += 1
        d += datetime.timedelta(days=1)
    return n


def missing_working_days(present, start: datetime.date, end: datetime.date,
                         holidays=None) -> List[datetime.date]:
    """Working days in [start, end] with no row.

    Staleness only looks at the newest date, so a day missing from the MIDDLE
    of a series is invisible to it — interbank_repo was missing 27-30 Sep
    inside its own range, and fx_rates_daily 77 working days.
    """
    holidays = holidays or set()
    have = set(present)
    out, d = [], start
    while d <= end:
        if d.weekday() not in (4, 5) and d not in holidays and d not in have:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out
