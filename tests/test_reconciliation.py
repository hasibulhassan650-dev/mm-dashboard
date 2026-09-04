"""
Reconciliation invariants — the ladder the desk trades MUST equal its source rows.

Locks the class of bug found Aug-2026: daily_net_flow (the materialised
liquidity ladder) can silently diverge from the live coupon/maturity/auction
rows when it is rebuilt from a different snapshot or bucketed on a different
date key. When that happens the forecast chart and the drilldown disagree and
the desk trades a number no event supports. These tests make integrity_check()
fail loud on that divergence — and confirm it stays quiet when they agree.
"""
import sys, os, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import db as dbmod
from db import Security, MaturityEvent, CouponEvent, AuctionEvent, DailyNetFlow
import validate

TODAY = datetime.date.today()
FWD = TODAY + datetime.timedelta(days=10)   # inside the 60-day recon window


def _seeded_engine(dnf_principal, event_principal):
    """One security maturing FWD for `event_principal`; a ladder row on the
    same date claiming `dnf_principal`. Equal ⇒ consistent; unequal ⇒ stale."""
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(Security(isin="BD0000000001", security_type="T_BOND",
                   outstanding_bdt_mill=event_principal))
    s.add(MaturityEvent(isin="BD0000000001", scheduled_date=FWD, payment_date=FWD,
                        principal_bdt_mill=event_principal))
    s.add(DailyNetFlow(flow_date=FWD, principal_inflow_bdt_mill=dnf_principal))
    s.commit(); s.close()
    return eng


def _run_check(monkeypatch, eng):
    monkeypatch.setattr(validate, "get_session",
                        lambda: sessionmaker(bind=eng)())
    return validate.integrity_check()


def test_consistent_ladder_has_no_recon_issue(monkeypatch):
    # ladder principal == the maturity event that backs it → silent
    eng = _seeded_engine(50000.0, 50000.0)
    rep = _run_check(monkeypatch, eng)
    assert rep["by_table"].get("ladder-recon", 0) == 0, \
        f"consistent ladder falsely flagged: {rep['issues']}"


def test_stale_ladder_flags_recon(monkeypatch):
    # ladder claims a 49bn inflow the events don't support (the real Aug case)
    eng = _seeded_engine(49262.4, 0.0)
    rep = _run_check(monkeypatch, eng)
    assert rep["by_table"].get("ladder-recon", 0) >= 1, \
        "stale ladder (49bn with no backing event) was NOT flagged"
    assert any("principal" in i and "ladder-recon" in i for i in rep["issues"])


def test_maturity_principal_must_equal_outstanding(monkeypatch):
    # a forward maturity whose principal drifts from the security's face is the
    # id-collision class: the redemption inflow shown to the desk is wrong
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(Security(isin="BD0000000002", security_type="T_BOND",
                   outstanding_bdt_mill=30000.0))
    s.add(MaturityEvent(isin="BD0000000002", scheduled_date=FWD, payment_date=FWD,
                        principal_bdt_mill=25000.0))   # drifted from 30000 face
    # keep the ladder consistent with the event so ONLY the face check fires
    s.add(DailyNetFlow(flow_date=FWD, principal_inflow_bdt_mill=25000.0))
    s.commit(); s.close()
    rep = _run_check(monkeypatch, eng)
    assert rep["by_table"].get("maturities", 0) >= 1, \
        "maturity principal ≠ outstanding face was NOT flagged"


# ── OMO cadence: a bank holiday must auto-suppress, an open-market gap must flag ──
import calendar_utils
from db import OMOTransaction, CallMoneyRate, RefRate


def _recent_working_days(n):
    """n working days ending at today-3 (inside the check's 21-day window),
    ascending — no seeded holidays in the in-memory DB, so weekends are the
    only non-working days."""
    d = datetime.date.today() - datetime.timedelta(days=3)
    out = []
    while len(out) < n:
        if calendar_utils.is_working_day(d, set()):
            out.append(d)
        d -= datetime.timedelta(days=1)
    return sorted(out)


def _seed_omo_edges(s, a, c):
    for d in (a, c):   # CB_REPO so the Tuesday-CB-Repo check never fires either
        s.add(OMOTransaction(transaction_date=d, instrument="CB_REPO", tenor_days=7,
                             accepted_bdt_crore=100.0, direction="INJECTION"))


def test_market_wide_closure_not_flagged_as_missing_omo(monkeypatch):
    # gap day B has NO omo AND no call money AND no ref rates → whole market shut
    # → a bank holiday the calendar need not list; must NOT fail the gate.
    a, b, c = _recent_working_days(3)
    eng = create_engine("sqlite:///:memory:"); dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)(); _seed_omo_edges(s, a, c); s.commit(); s.close()
    rep = _run_check(monkeypatch, eng)
    assert not any("no OMO operation" in i and str(b) in i for i in rep["issues"]), \
        f"market-wide closure {b} was wrongly flagged: {rep['issues']}"


def test_missing_omo_on_open_market_day_is_flagged(monkeypatch):
    # same gap, but the market TRADED that day (call money + ref rates present)
    # while OMO is absent → a real missing/mislabeled release; must flag.
    a, b, c = _recent_working_days(3)
    eng = create_engine("sqlite:///:memory:"); dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    _seed_omo_edges(s, a, c)
    s.add(CallMoneyRate(trade_date=b))
    s.add(RefRate(trade_date=b, rate_type="DOMMR", product="Overnight"))
    s.commit(); s.close()
    rep = _run_check(monkeypatch, eng)
    assert any("no OMO operation" in i and str(b) in i for i in rep["issues"]), \
        f"missing OMO on an open-market day {b} should be flagged: {rep['issues']}"
