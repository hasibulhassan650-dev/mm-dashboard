"""
Sep-2026 accuracy audit — the defect classes it found, locked in.

Each test encodes a fact about how BB actually publishes data that the code
got wrong, so that a future "simplification" cannot quietly bring it back:

  1. BB's treasury results page prints the ISSUE date (T+1 working day), not
     the auction date. Storing it as the auction date put every auction one
     working day late and made the calendar and the results unjoinable.
  2. Results must CONFIRM calendar auctions (actual accepted amount, actual
     issue date) — otherwise the liquidity ladder books the planned amount on
     a computed date forever, and ad-hoc auctions BB adds never appear.
  3. The government declares some Saturdays working days (23-May-2026); the
     market trades, settlements land, and the calendar must know.
  4. MLS has no rate signature (9.50 in Jun–Jul 2026, 5.25 from Aug) — a
     "correcting" fingerprint turned two real MLS rows into AR.
  5. SLF is overnight-only: a multi-day "SLF" line at an Islamic rate is IBLF.
  6. Corridor changes reprice BB's operations with a lag (Aug-2026: a week),
     so a rate-vs-policy gate needs a grace window, not a same-day assertion.
  7. A market-wide silent weekday not in the holiday table is a missing
     closure; a listed holiday with prints is a wrong seed.
"""
import sys, os, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import db as dbmod
from db import (AuctionEvent, PrimaryYieldSnapshot, HolidayCalendar, CallMoneyRate,
                PolicyRateHistory, OMOTransaction)
import calendar_utils
import validate
from fetchers.omo import _fingerprint
from engines.pipeline import confirm_auctions_from_results

D = datetime.date
TODAY = D.today()


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _run_check(monkeypatch, eng):
    monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
    return validate.integrity_check()


# ── 1 & 3. calendar: working weekends and previous_working_day ───────────────

class TestCalendar:
    def setup_method(self):
        calendar_utils.load_holidays(set(), set())

    def teardown_method(self):
        calendar_utils.load_holidays(set(), set())

    def test_declared_working_saturday_is_a_working_day(self):
        # 23-May-2026 (Sat) was declared working; call money, ref rates and an
        # FX auction all printed. A coupon due that day is PAID that day.
        sat = D(2026, 5, 23)
        assert not calendar_utils.is_working_day(sat)
        calendar_utils.load_holidays(set(), {sat})
        assert calendar_utils.is_working_day(sat)
        assert calendar_utils.get_next_working_day(sat)["result_date"] == sat

    def test_load_calendar_rows_separates_working_days_from_holidays(self):
        rows = [(D(2026, 5, 23), "WORKING_DAY"), (D(2026, 5, 25), "INFERRED_CLOSURE")]
        calendar_utils.load_calendar_rows(rows)
        assert calendar_utils.is_working_day(D(2026, 5, 23))       # Saturday, declared working
        assert not calendar_utils.is_working_day(D(2026, 5, 25))   # Monday, Eid closure

    def test_previous_working_day_recovers_auction_from_issue(self):
        # bills: issued Monday → auctioned Sunday; bonds: Wednesday → Tuesday
        assert calendar_utils.previous_working_day(D(2026, 9, 7)) == D(2026, 9, 6)
        assert calendar_utils.previous_working_day(D(2026, 9, 9)) == D(2026, 9, 8)
        # Sunday issue → Thursday auction (Fri/Sat weekend skipped)
        assert calendar_utils.previous_working_day(D(2026, 9, 13)) == D(2026, 9, 10)

    def test_previous_working_day_skips_holiday_block(self):
        # 10Y issued Tue 24-Mar-2026 after the 17–23 Mar closures → auction Mon 16-Mar,
        # exactly the calendar slot. (BB's calendar was right; our dates were off.)
        calendar_utils.load_holidays({D(2026, 3, 17), D(2026, 3, 18), D(2026, 3, 19),
                                      D(2026, 3, 22), D(2026, 3, 23)})
        assert calendar_utils.previous_working_day(D(2026, 3, 24)) == D(2026, 3, 16)

    def test_previous_working_day_uses_declared_working_saturday(self):
        # pre-Eid bills issued Sun 24-May-2026: the auction was the declared
        # working Saturday, not the Thursday.
        calendar_utils.load_holidays(set(), {D(2026, 5, 23)})
        assert calendar_utils.previous_working_day(D(2026, 5, 24)) == D(2026, 5, 23)


# ── 2. results confirm the calendar ──────────────────────────────────────────

class TestConfirmAuctions:
    def _cal(self, s, adate, tenor, offered, settle):
        a = AuctionEvent(fiscal_year="2026-27", auction_no="1", auction_date=adate,
                         settlement_date=settle, security_type="T_BILL", tenor_label=tenor,
                         offered_amount_bdt_crore=offered, offered_amount_bdt_mill=offered * 10,
                         outflow_status="PLANNED", source="seed")
        s.add(a); return a

    def _res(self, s, adate, issue, tenor, acc, cutoff=8.5):
        s.add(PrimaryYieldSnapshot(snapshot_date=issue, auction_date=adate, issue_date=issue,
                                   security_type="T_BILL", tenor_label=tenor, tenor_years=0.25,
                                   cutoff_yield_pct=cutoff, offered_bdt_crore=acc, accepted_bdt_crore=acc))

    def test_result_confirms_accepted_amount_and_issue_date(self):
        # calendar said 3,500 settling Mon; BB accepted 5,000 (over-allotment)
        eng, s = _mem()
        a = self._cal(s, D(2026, 3, 29), "91D", 3500.0, D(2026, 3, 30))
        self._res(s, D(2026, 3, 29), D(2026, 3, 30), "91D", 5000.0)
        s.commit()
        r = confirm_auctions_from_results(s); s.commit()
        assert r["confirmed"] == 1
        assert a.outflow_status == "CONFIRMED"
        assert a.accepted_amount_bdt_crore == 5000.0 and a.accepted_amount_bdt_mill == 50000.0
        assert a.settlement_date == D(2026, 3, 30)

    def test_moved_auction_takes_bb_issue_date_not_computed_one(self):
        # calendar slot Sun 24-May with settlement computed as 01-Jun (Eid block);
        # BB actually held it Sat 23-May and issued 24-May.
        eng, s = _mem()
        a = self._cal(s, D(2026, 5, 24), "91D", 3500.0, D(2026, 6, 1))
        self._res(s, D(2026, 5, 23), D(2026, 5, 24), "91D", 3500.0)
        s.commit()
        r = confirm_auctions_from_results(s); s.commit()
        assert r["moved"] == 1 and a.settlement_date == D(2026, 5, 24)
        assert "per BB results" in a.roll_reason

    def test_exact_date_wins_and_adhoc_auction_is_added(self):
        # 29-Mar calendar bill + an EXTRA 91D BB ran on 01-Apr (no calendar row).
        # The extra result must NOT steal the 29-Mar row; it gets its own.
        eng, s = _mem()
        a = self._cal(s, D(2026, 3, 29), "91D", 3500.0, D(2026, 3, 30))
        self._res(s, D(2026, 4, 1), D(2026, 4, 2), "91D", 5000.0)     # ad hoc, inserted first on purpose
        self._res(s, D(2026, 3, 29), D(2026, 3, 30), "91D", 3500.0)
        s.commit()
        r = confirm_auctions_from_results(s); s.commit()
        assert r["added"] == 1 and a.accepted_amount_bdt_crore == 3500.0
        extra = s.query(AuctionEvent).filter_by(auction_date=D(2026, 4, 1)).one()
        assert extra.outflow_status == "CONFIRMED" and extra.settlement_date == D(2026, 4, 2)
        assert extra.accepted_amount_bdt_crore == 5000.0
        assert "uncalendared" in extra.source

    def test_rerun_is_idempotent(self):
        eng, s = _mem()
        self._cal(s, D(2026, 3, 29), "91D", 3500.0, D(2026, 3, 30))
        self._res(s, D(2026, 4, 1), D(2026, 4, 2), "91D", 5000.0)
        self._res(s, D(2026, 3, 29), D(2026, 3, 30), "91D", 3500.0)
        s.commit()
        confirm_auctions_from_results(s); s.commit()
        r2 = confirm_auctions_from_results(s); s.commit()
        assert r2 == {"confirmed": 2, "moved": 0, "added": 0}
        assert s.query(AuctionEvent).count() == 2


# ── 4 & 5. OMO fingerprint ───────────────────────────────────────────────────

class TestFingerprintRegressions:
    def test_mls_at_9_5_is_left_alone(self):
        # 10-Jun / 08-Jul-2026: BB printed "MLS 28-Days ... 9.50". Not AR.
        assert _fingerprint("MLS", 28, 9.5, None, D(2026, 6, 10)) is None
        assert _fingerprint("MLS", 28, 5.25, None, D(2026, 8, 9)) is None

    def test_ar_at_5_25_is_not_forced_to_mls(self):
        # no rule may turn a 28D@5.25 into MLS by rate alone either
        got = _fingerprint("AR", 28, 5.25, None, D(2026, 8, 9))
        assert got is None or got[0] != "MLS"

    def test_multi_day_slf_at_islamic_rate_is_iblf(self):
        # SLF is an overnight standing facility; "SLF 7-Days @5.25" is the IBLF
        # 7-day line that inherited the SLF block name.
        assert _fingerprint("SLF", 7, 5.25, None, D(2026, 5, 12)) == ("IBLF", "INJECTION")
        assert _fingerprint("SLF", 1, 11.5, None, D(2026, 5, 12)) is None


# ── 6 & 7. integrity gate rules ──────────────────────────────────────────────

def _seed_policy(s):
    s.add(PolicyRateHistory(effective_date=D(2026, 2, 9), repo=10.0, slf=11.5, sdf=7.5, verified=True))
    s.add(PolicyRateHistory(effective_date=D(2026, 8, 2), repo=9.5, slf=11.0, sdf=7.5, verified=True))


def _omo(s, d, inst, ten, rate, acc=1000.0):
    s.add(OMOTransaction(transaction_date=d, maturity_date=d + datetime.timedelta(days=ten),
                         instrument=inst, tenor_label=f"{ten}D", tenor_days=ten,
                         accepted_bdt_crore=acc, rate_pct=rate, direction="INJECTION"))


def test_omo_policy_gate_flags_stale_rate_but_not_repricing_lag(monkeypatch):
    eng, s = _mem(); _seed_policy(s)
    _omo(s, D(2026, 8, 4), "CB_REPO", 7, 10.0)    # 2 days after the cut: BB really printed 10.00
    _omo(s, D(2026, 8, 18), "CB_REPO", 7, 10.0)   # 16 days after: this would be a real error
    _omo(s, D(2026, 8, 17), "AR", 1, 10.0)        # overnight fine-tuning prices above the 7D rate
    s.commit()
    rep = _run_check(monkeypatch, eng)
    pol = [i for i in rep["issues"] if i.startswith("omo-policy")]
    assert len(pol) == 1 and "2026-08-18" in pol[0], pol


def test_calendar_gate_flags_silent_weekday_and_wrong_holiday(monkeypatch):
    eng, s = _mem()
    lo = D(2025, 7, 1)
    silent = D(2025, 12, 16)      # Victory Day (Tue): the market did not trade
    wrong = D(2025, 12, 17)       # listed as a holiday, but the market traded
    s.add(HolidayCalendar(calendar_date=wrong, holiday_name="x", holiday_type="GOVT_GAZETTE"))
    d = lo
    while d <= TODAY:
        if d.weekday() in (0, 1, 2, 3, 6) and d != silent:
            s.add(CallMoneyRate(trade_date=d, product="Overnight", maturity_days=1,
                                amount_crore=1.0, average_rate_pct=9.0))
        d += datetime.timedelta(days=1)
    s.commit()
    rep = _run_check(monkeypatch, eng)
    cal = [i for i in rep["issues"] if i.startswith("calendar")]
    assert any(str(silent) in i and "silent" in i for i in cal), cal
    assert any(str(wrong) in i and "wrong seed" in i for i in cal), cal
    assert len([i for i in cal if "silent" in i]) == 1


def test_calendar_gate_recognises_declared_working_saturday(monkeypatch):
    eng, s = _mem()
    sat = D(2026, 5, 23)
    s.add(CallMoneyRate(trade_date=sat, product="Overnight", maturity_days=1,
                        amount_crore=1.0, average_rate_pct=9.0))
    d = D(2025, 7, 1)
    while d <= TODAY:                                   # keep every weekday active
        if d.weekday() in (0, 1, 2, 3, 6):
            s.add(CallMoneyRate(trade_date=d, product="Overnight", maturity_days=1,
                                amount_crore=1.0, average_rate_pct=9.0))
        d += datetime.timedelta(days=1)
    s.commit()
    rep = _run_check(monkeypatch, eng)
    assert any("2026-05-23" in i and "WORKING_DAY" in i for i in rep["issues"]), rep["issues"]
    s.add(HolidayCalendar(calendar_date=sat, holiday_name="working", holiday_type="WORKING_DAY")); s.commit()
    rep = _run_check(monkeypatch, eng)
    assert not any("2026-05-23" in i for i in rep["issues"])


def test_ledger_exempts_mls_partial_repayments(monkeypatch):
    # 09-Aug MLS 1,340 @5.25/28D came back as 83 (25-Aug) + 1,262.40 (06-Sep).
    # Per-day matching would call 06-Sep a phantom tranche. It is not.
    eng, s = _mem()
    placed = TODAY - datetime.timedelta(days=33)
    mat = placed + datetime.timedelta(days=28)
    s.add(OMOTransaction(transaction_date=placed, maturity_date=mat, instrument="MLS",
                         tenor_label="28D", tenor_days=28, accepted_bdt_crore=1340.0,
                         rate_pct=5.25, direction="INJECTION"))
    s.add(OMOTransaction(transaction_date=mat, maturity_date=mat, instrument="MLS",
                         tenor_label="28D", tenor_days=28, accepted_bdt_crore=0.0,
                         maturity_bdt_crore=1262.40, rate_pct=5.25, direction="INJECTION"))
    s.commit()
    rep = _run_check(monkeypatch, eng)
    assert rep["by_table"].get("omo-ledger", 0) == 0, rep["issues"]


def test_yield_rows_must_carry_issue_date_after_auction_date(monkeypatch):
    eng, s = _mem()
    s.add(PrimaryYieldSnapshot(snapshot_date=D(2026, 9, 7), auction_date=D(2026, 9, 6),
                               issue_date=D(2026, 9, 7), security_type="T_BILL",
                               tenor_label="91D", tenor_years=0.25, cutoff_yield_pct=8.59))
    s.add(PrimaryYieldSnapshot(snapshot_date=D(2026, 9, 9), auction_date=D(2026, 9, 9),
                               issue_date=None, security_type="T_BOND",
                               tenor_label="5Y", tenor_years=5, cutoff_yield_pct=8.64))
    s.commit()
    rep = _run_check(monkeypatch, eng)
    ys = [i for i in rep["issues"] if i.startswith("yields")]
    assert any("2026-09-09 5Y" in i and "no issue_date" in i for i in ys), ys
    assert not any("2026-09-06" in i for i in ys)


# ── 8. BB re-published a stale table under a new date ────────────────────────

def _txn(d, inst, ten, acc, mat, pub, src="x.pdf"):
    return {"transaction_date": d, "maturity_date": d + datetime.timedelta(days=ten), "instrument": inst,
            "tenor_label": f"{ten}D", "tenor_days": ten, "accepted_bdt_crore": acc, "maturity_bdt_crore": mat,
            "rate_pct": 4.0, "rate_range": None, "direction": "INJECTION", "source_pdf": src,
            "source_pub_date": pub, "source_serial": None}


def test_store_refuses_release_that_copies_another_dates_table():
    # 05-Apr operation stored; the "as on 04 May" file carries the SAME five
    # rows. It must not become a second, phantom operation.
    from engines.pipeline import _store_omo_txns
    eng, s = _mem()
    apr = D(2026, 4, 5); may = D(2026, 5, 4)
    rows = [("AR", 28, 0.0, 3784.35), ("IBLF", 7, 100.0, 200.15), ("IBLF", 14, 70.0, 0.0),
            ("IBLF", 28, 2587.0, 2878.28), ("SDF", 1, 2013.0, 3377.08)]
    _store_omo_txns(s, [_txn(apr, *r, pub=D(2026, 4, 6)) for r in rows], datetime.datetime.utcnow()); s.commit()
    saved, _ = _store_omo_txns(s, [_txn(may, *r, pub=D(2026, 5, 5)) for r in rows], datetime.datetime.utcnow()); s.commit()
    assert saved == 0
    assert s.query(OMOTransaction).filter_by(transaction_date=may).count() == 0
    # a genuinely different 04-May operation is still accepted
    saved, _ = _store_omo_txns(s, [_txn(may, "SDF", 1, 1375.0, 0.0, pub=D(2026, 5, 5))], datetime.datetime.utcnow()); s.commit()
    assert saved == 1


def test_gate_flags_duplicate_content_dates(monkeypatch):
    eng, s = _mem()
    for d in (D(2026, 4, 5), D(2026, 5, 4)):
        for inst, ten, acc, mat in [("IBLF", 7, 100.0, 200.15), ("IBLF", 28, 2587.0, 2878.28), ("SDF", 1, 2013.0, 3377.08)]:
            s.add(OMOTransaction(transaction_date=d, maturity_date=d + datetime.timedelta(days=ten), instrument=inst,
                                 tenor_label=f"{ten}D", tenor_days=ten, accepted_bdt_crore=acc,
                                 maturity_bdt_crore=mat, rate_pct=4.0, direction="INJECTION"))
    s.commit()
    rep = _run_check(monkeypatch, eng)
    assert any("verbatim copy of 2026-04-05" in i for i in rep["issues"]), rep["issues"]


def test_multi_day_slf_line_is_iblf_even_without_a_rate():
    # BB prints IBLF's first row as an unlabelled "7-Days" line under "SLF 1-Day";
    # a maturity-only line there has no rate at all, and is still IBLF's.
    assert _fingerprint("SLF", 7, None, None, D(2026, 4, 26)) == ("IBLF", "INJECTION")
    assert _fingerprint("SLF", 14, None, None, D(2026, 3, 12)) == ("IBLF", "INJECTION")
    assert _fingerprint("SLF", 1, None, None, D(2026, 4, 26)) is None
