"""The five problems the live system still showed on 18-Nov-2026, each replayed from its real evidence.

1 a Sukuk release was judged by the BGIIB table and escalated for ever;  2 a BGIIB PDF BB lists but never serves;
3 GitHub's blocked runs flipping a PC-healthy step red;  4 a scheme's size (Tk 10,000 crore) filling the amount of a Tk 4.50 crore
disbursement;  5 a background sentence announced as BB bank support.
"""
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import BgiibAuction, DataAbsence, DataChange, LiquidityEvent, PressRelease, StepRun, SukukOutstanding
from engines import doctor as DR
from engines import failures as F
from engines import liquidity_news as L
from engines import news_rules as R
from engines import news_triage as T
from engines import step_ledger as SL

D = datetime.date
TODAY = datetime.date.today()


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


def _report(monkeypatch, eng):
    import validate
    monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
    return validate.integrity_check(limit_per_rule=200)


def _old_omo(s):
    old = TODAY - datetime.timedelta(days=300)
    s.add(dbmod.OMOTransaction(transaction_date=old, maturity_date=old, instrument="SDF", tenor_label="1D", tenor_days=1,
                               accepted_bdt_crore=1.0, maturity_bdt_crore=0.0, rate_pct=7.5, direction="ABSORPTION"))


# ── 1 ─ a Sukuk release is not a BGIIB result ──────────────────────────────────
class TestASukukReleaseIsNotJudgedByTheBgiibTable:
    def _world(self, pub_days_ago, listed):
        eng, s = _mem()
        _old_omo(s)
        pub = TODAY - datetime.timedelta(days=pub_days_ago)
        s.add(PressRelease(serial="pr14384", published_date=pub, subject_date=pub, category="SUKUK", url="https://x/s.pdf",
                           title="Press Release for the 2nd Short-term Bangladesh Government Investment Sukuk Auction"))
        if listed:
            s.add(SukukOutstanding(isin="BDS1", project="Short Term BGIS", issue_date=pub + datetime.timedelta(days=3),
                                   maturity_date=pub + datetime.timedelta(days=276), outstanding_crore=5000.0))
        s.commit()
        return eng

    def test_it_no_longer_escalates_as_a_missing_bgiib_auction(self, monkeypatch):
        """pr14384 (a Sukuk auction) can never have a bgiib_auctions row: it was a permanent ESCALATED defect."""
        rep = _report(monkeypatch, self._world(6, listed=False))
        assert not [i for i in rep["issues"] if i.startswith("bgiib")], rep["issues"]

    def test_it_waits_for_bbs_sukuk_page_then_escalates(self, monkeypatch):
        young = _report(monkeypatch, self._world(6, listed=False))
        assert [w for w in young["waiting"] if w.startswith("sukuk")] and not [i for i in young["issues"] if i.startswith("sukuk")]
        import validate
        old = _report(monkeypatch, self._world(validate.SUKUK_GRACE_DAYS + 3, listed=False))
        assert [i for i in old["issues"] if i.startswith("sukuk") and "ESCALATED" in i]

    def test_an_issue_on_bbs_sukuk_page_closes_it(self, monkeypatch):
        rep = _report(monkeypatch, self._world(10, listed=True))
        assert not [x for x in rep["issues"] + rep["waiting"] if x.startswith("sukuk")]

    def test_the_bgiib_fetch_never_downloads_a_sukuk_pdf_as_a_bgiib_table(self):
        src = open(os.path.join(_ROOT, "fetchers", "islamic.py"), encoding="utf-8").read()
        assert "WHERE p.category = 'BGIIB'" in src and "IN ('BGIIB', 'SUKUK')" not in src


# ── 2 ─ a BGIIB PDF BB lists but never serves ──────────────────────────────────
class TestADeadBbPdfBecomesARecordedHoleWithEvidence:
    NOW = datetime.datetime(2026, 11, 18, 15, 0)

    def _world(self, failed_reads=4, later_served=True, age_days=6):
        eng, s = _mem()
        pub = self.NOW.date() - datetime.timedelta(days=age_days)
        s.add(PressRelease(serial="pr14381", published_date=pub, subject_date=pub - datetime.timedelta(days=2), category="BGIIB",
                           title="BGIIB Auction Result held on 10 November 2026", url="https://x/pr14381.pdf"))
        if later_served:
            s.add(PressRelease(serial="pr14395", published_date=self.NOW.date(), subject_date=self.NOW.date() - datetime.timedelta(days=1),
                               category="BGIIB", title="BGIIB Auction Result held on 17 November 2026", url="https://x/pr14395.pdf"))
            s.add(BgiibAuction(auction_date=self.NOW.date() - datetime.timedelta(days=1), tenor_label="6M", accepted_crore=205.0,
                               source_serial="pr14395"))
        for i in range(failed_reads):
            s.add(StepRun(run_utc=self.NOW - datetime.timedelta(hours=30 * i + 1), machine="pc", step="bgiib", status="failed",
                          error_class=F.PDF_UNSERVED,
                          error="bgiib: no BGIIB figures parsed from any release; refused: {'pr14381: download failed (RuntimeError)': 1}"))
        s.commit()
        return eng, s

    def test_after_the_grace_with_a_later_pdf_served_it_is_recorded_as_bbs_hole_with_evidence(self):
        eng, s = self._world()
        out = DR.heal_unserved_bgiib(s, self.NOW)
        s.commit()
        assert len(out["recorded"]) == 1
        a = s.query(DataAbsence).filter_by(dataset="bgiib").one()
        assert a.kind == "bb_omission" and a.reason.startswith(DR.UNSERVED_MARK)
        assert "pr14381" in a.reason and "pr14395" in a.reason and "4 reads" in a.reason
        assert s.query(DataChange).filter_by(change_class="unserved_pdf").count() == 1

    def test_it_is_idempotent(self):
        eng, s = self._world()
        DR.heal_unserved_bgiib(s, self.NOW)
        s.commit()
        assert DR.heal_unserved_bgiib(s, self.NOW)["recorded"] == []
        assert s.query(DataAbsence).filter_by(dataset="bgiib").count() == 1

    def test_no_hole_is_recorded_while_bb_serves_no_later_pdf_either(self):
        """If nothing of BB's downloads, this is an outage or our block, not one dead link."""
        eng, s = self._world(later_served=False)
        assert DR.heal_unserved_bgiib(s, self.NOW)["recorded"] == []

    def test_no_hole_before_the_grace_or_with_too_few_failed_reads(self):
        assert DR.heal_unserved_bgiib(self._world(age_days=2)[1], self.NOW)["recorded"] == []
        assert DR.heal_unserved_bgiib(self._world(failed_reads=1)[1], self.NOW)["recorded"] == []

    def test_the_hole_retires_itself_when_bb_publishes_the_figures(self):
        eng, s = self._world()
        DR.heal_unserved_bgiib(s, self.NOW)
        s.commit()
        s.add(BgiibAuction(auction_date=D(2026, 11, 10), tenor_label="3M", accepted_crore=60.0, source_serial="pr14381"))
        s.commit()
        out = DR.heal_unserved_bgiib(s, self.NOW)
        s.commit()
        assert len(out["retired"]) == 1 and s.query(DataAbsence).filter_by(dataset="bgiib").count() == 0
        assert s.query(DataChange).filter_by(change_class="unserved_pdf_retired").count() == 1

    def test_validate_shows_the_recorded_hole_as_a_notice_not_a_defect(self, monkeypatch):
        eng, s = self._world()
        DR.heal_unserved_bgiib(s, self.NOW)
        s.commit()
        pub = TODAY - datetime.timedelta(days=6)
        s.query(PressRelease).filter_by(serial="pr14381").update({"published_date": pub, "subject_date": pub - datetime.timedelta(days=2)})
        s.query(DataAbsence).filter_by(dataset="bgiib").update({"absent_date": pub - datetime.timedelta(days=2)})
        s.commit()
        rep = _report(monkeypatch, eng)
        assert not [i for i in rep["issues"] if i.startswith("bgiib") and "pr14381" in i]
        assert [n for n in rep["notices"] if n.startswith("bgiib") and "pr14381" in n and "never served" in n]


# ── 3 ─ GitHub's blocked runs cannot flip a PC-healthy step ────────────────────
class TestHealthIsJudgedFromThePcWhenThePcIsReading:
    NOW = datetime.datetime(2026, 11, 18, 15, 0)

    def _add(self, s, machine, status, hours_ago, cls=None):
        s.add(StepRun(run_utc=self.NOW - datetime.timedelta(hours=hours_ago), machine=machine, step="bgiib", status=status, error_class=cls,
                      error="x" if status != "ok" else None))
        s.commit()

    def test_a_github_failure_newer_than_the_pcs_ok_does_not_turn_the_step_red(self):
        eng, s = _mem()
        self._add(s, "pc", "ok", 3)
        self._add(s, "github", "failed", 1, F.UNKNOWN)
        assert SL.step_health(s, "bgiib", self.NOW)[1] is True

    def test_a_pc_failure_still_counts_and_github_alone_still_stands_in_when_the_pc_is_silent(self):
        eng, s = _mem()
        self._add(s, "pc", "ok", 5)
        self._add(s, "pc", "failed", 1, F.PDF_UNSERVED)
        assert SL.step_health(s, "bgiib", self.NOW)[1] is False
        eng2, s2 = _mem()
        self._add(s2, "github", "ok", 3)
        assert SL.step_health(s2, "bgiib", self.NOW)[1] is True

    def test_the_four_github_error_texts_are_classified_not_unknown(self):
        assert F.classify("bgiib: no BGIIB figures parsed from any release; refused: {'pr14384: not a PDF': 1}") == F.PDF_UNSERVED
        assert F.classify("circulars: the circular page was not served (challenge or no table)") == F.BB_BLOCKED
        assert F.classify("policy: policy fetch returned nothing (page unparseable)") == F.EMPTY
        assert F.classify("monetary: 'NoneType' object has no attribute 'find_all'") == F.PARSE_DEGRADED


# ── 4 ─ a scheme's size must not fill the amount of a disbursement ─────────────
KUSHTIA = ("Forty farmers and entrepreneurs in Kushtia received loans totalling Tk4.50 crore under Bangladesh Bank's Tk10,000 crore "
           "refinancing scheme at a town hall and stakeholder consultation led by NRBC Bank PLC.")


SECOND = "He said 43 scheduled banks nationwide had signed agreements with Bangladesh Bank to implement the Tk10,000 crore scheme."


class TestASchemeSizeNeverFillsTheAmountOfADisbursement:
    """The model read ONE article as two events: the disbursement ('received loans totalling Tk4.50 crore ...') and, from a different
    sentence, the scheme's size ('43 banks signed agreements to implement the Tk10,000 crore scheme', APPROVED). The second merged
    into the first through the shared-article rule and filled its amount with 10,000 crore - and the row stayed DISBURSED, so Tk 10,000
    crore of cash that never moved was summed as injected."""

    def _art(self):
        body = KUSHTIA + " " + SECOND
        return {"url": "https://x/kushtia", "source": "dailystar_business", "title": "NRBC Bank refinance loans in Kushtia",
                "summary": "", "body_text": body, "published_utc": datetime.datetime(2026, 11, 18, 9),
                "fetched_utc": datetime.datetime(2026, 11, 18, 9)}

    def _raw(self, amount, quote, status):
        return {"kind": "BB_REFINANCE", "direction": "INJECT", "amount_crore": amount, "is_cumulative": False, "status": status,
                "channel": "BB_CREDIT", "counterparty": "NRBC Bank PLC" if status == "DISBURSED" else None, "summary": "s",
                "evidence_quote": quote, "confidence": 1.0, "event_date": "2026-11-18"}

    def _store(self, s, raws):
        art = self._art()
        stats = {"events_created": 0, "events_merged": 0, "events_rejected": 0, "reasons": []}
        T._store_events(s, raws, art, datetime.datetime(2026, 11, 18, 10), stats, "llm", art["url"])
        s.commit()

    def test_received_loans_totalling_attaches_the_amount_of_that_act(self):
        assert R._amount_for(KUSHTIA, "BB_REFINANCE") == 4.5

    def test_the_two_facts_stay_two_rows_and_no_row_is_a_10000_crore_disbursement(self):
        eng, s = _mem()
        self._store(s, [self._raw(4.5, KUSHTIA, "DISBURSED"), self._raw(10000, SECOND, "APPROVED")])
        rows = s.query(LiquidityEvent).all()
        assert len(rows) == 2, "one article, two facts"
        assert not [r for r in rows if r.amount_crore == 10000.0 and r.status == "DISBURSED"]
        paid = [r for r in rows if r.status == "DISBURSED"]
        assert len(paid) == 1 and paid[0].amount_crore == 4.5 and not paid[0].matters, "Tk 4.50 crore is under the 500 crore line"

    def test_an_amount_the_article_did_not_state_for_the_act_is_not_filled_in_from_another_sentence(self):
        """The incident state exactly: the first event's amount was blank (the guard could not attach one), and the scheme's size, from
        another sentence of the same article, must not fill it."""
        eng, s = _mem()
        self._store(s, [self._raw(None, KUSHTIA, "DISBURSED"), self._raw(10000, SECOND, "APPROVED")])
        rows = s.query(LiquidityEvent).all()
        assert len(rows) == 2
        paid = [r for r in rows if r.status == "DISBURSED"]
        assert len(paid) == 1 and paid[0].amount_crore is None, "left blank, not borrowed from the scheme size"

    def test_without_the_fix_the_second_event_would_have_filled_the_first(self):
        """Proof the test can fail: merging with no exclusion reproduces the incident."""
        eng, s = _mem()
        art = self._art()
        a, _ = L.merge_or_create(s, L.validate_event(self._raw(None, KUSHTIA, "DISBURSED"), art)[0], art, datetime.datetime(2026, 11, 18, 10))
        row, how = L.merge_or_create(s, L.validate_event(self._raw(10000, SECOND, "APPROVED"), art)[0], art, datetime.datetime(2026, 11, 18, 10))
        assert how == "merged" and row.id == a.id and row.amount_crore == 10000.0 and row.status == "DISBURSED"
        row2, how2 = L.merge_or_create(s, L.validate_event(self._raw(10000, SECOND, "APPROVED"), art)[0], art,
                                       datetime.datetime(2026, 11, 18, 10), exclude_ids=[a.id])
        assert how2 == "created" and row2.id != a.id

    def test_an_exact_repeat_of_the_same_event_in_one_article_is_still_one_row(self):
        eng, s = _mem()
        self._store(s, [self._raw(4.5, KUSHTIA, "DISBURSED"), self._raw(4.5, KUSHTIA, "DISBURSED")])
        assert s.query(LiquidityEvent).count() == 1

    def test_the_model_amount_is_checked_against_the_amount_attached_to_the_act(self):
        assert L.verify_amount(10000, KUSHTIA, "BB_REFINANCE") == (None, True)
        assert L.verify_amount(4.5, KUSHTIA, "BB_REFINANCE") == (4.5, False)


# ── 5 ─ a background sentence is not news ──────────────────────────────────────
class TestABackgroundSentenceIsNotAnnounced:
    def test_an_amountless_running_total_of_bank_support_does_not_matter(self):
        ev = {"kind": "BB_BANK_SUPPORT", "confidence": 0.9, "amount_crore": None, "is_cumulative": True}
        assert L.matters(ev) is False

    def test_a_single_act_of_bank_support_without_an_amount_still_matters(self):
        """BB lending to a named bank matters whatever its size - unless it is only background."""
        assert L.matters({"kind": "BB_BANK_SUPPORT", "confidence": 0.9, "amount_crore": None, "is_cumulative": False}) is True

    def test_a_crr_change_still_matters_without_an_amount(self):
        assert L.matters({"kind": "RESERVE_REQ_CUT", "confidence": 0.9, "amount_crore": None, "is_cumulative": True}) is True
