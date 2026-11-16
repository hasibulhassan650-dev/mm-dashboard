"""Bangla news, checked by code. The language model may READ a Bangla article; what it reports is never trusted as
given: the amount is re-read from the quote, a request is not a payment, a dollar figure is not taka. These guards were
English-only, and a Bangla 'requested' would have passed as a payment.
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
from db import NewsArticle
from engines import bangla as B
from engines import liquidity_news as L
from engines import news_triage as T

NOW = datetime.datetime(2026, 11, 16, 12, 0, 0)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


class TestAnAmountIsReadByCode:
    def test_digits_and_units(self):
        assert B.amounts_crore("২৭ হাজার কোটি টাকা") == [27000.0]
        assert B.amounts_crore("১,২০০ কোটি টাকা ছাড়") == [1200.0]
        assert B.amounts_crore("৫০ লাখ টাকা") == [0.5], "a lakh is 0.01 crore"
        assert B.amounts_crore("দেড় লাখ কোটি টাকা") == [150000.0]

    def test_fraction_words(self):
        assert B.amounts_crore("সাড়ে ৩ হাজার কোটি টাকা") == [3500.0]
        assert B.amounts_crore("আড়াই হাজার কোটি টাকা") == [2500.0]
        assert B.amounts_crore("সোয়া ৩ কোটি টাকা") == [3.25]
        assert B.amounts_crore("পৌনে ৩ কোটি টাকা") == [2.75]

    def test_crore_dollars_is_not_taka(self):
        """'২ কোটি ডলার' is 20 million DOLLARS: reading it as 2 crore taka would be wrong by two orders of magnitude."""
        assert B.amounts_crore("২ কোটি ডলার") == [] and B.amounts_crore("৫০ কোটি মার্কিন ডলার") == []

    def test_a_compound_number_is_not_read_by_its_tail(self):
        """'৩ হাজার ৫০০ কোটি টাকা' is 3,500 crore; reading only the tail would store 500."""
        assert B.amounts_crore("৩ হাজার ৫০০ কোটি টাকা") == []

    def test_an_amount_without_a_currency_is_not_read(self):
        assert B.amounts_crore("৫০০ কোটি") == []

    def test_zero_width_joiners_and_nfc_do_not_break_matching(self):
        assert B.normalize("টাকা\u200c") == "টাকা"
        assert L.amounts_crore("২৭\u200c হাজার কোটি টাকা") == [27000.0]


class TestMeaningIsCheckedByCode:
    def test_a_request_is_not_a_payment(self):
        assert B.is_request("পেট্রোবাংলা ২৭ হাজার কোটি টাকা চেয়েছে")

    def test_done_versus_future(self):
        assert B.tense("অর্থ বিভাগ ৩ হাজার কোটি টাকা ছাড় করেছে") == (True, False)
        assert B.tense("অর্থ বিভাগ ৩ হাজার কোটি টাকা ছাড় করবে") == (False, True)

    def test_a_running_total_is_not_new_cash(self):
        assert B.is_stock("এ পর্যন্ত ৭৬ হাজার কোটি টাকা দেওয়া হয়েছে")

    def test_language_detection(self):
        assert B.is_bangla("বাংলাদেশ ব্যাংক ঋণ দিয়েছে") and not B.is_bangla("Bangladesh Bank released Tk 3,000 crore")


class TestTheGuardsApplyToAModelsBanglaReading:
    ART = {"url": "https://x/bn", "source": "prothomalo_bn_business", "title": "ভর্তুকি", "summary": "",
           "published_utc": NOW, "fetched_utc": NOW}

    def _ev(self, quote, amount=None, kind="SUBSIDY_RELEASE", status="DISBURSED"):
        art = dict(self.ART, body_text=f"{quote} এটি একটি প্রতিবেদনের অংশ।")
        raw = {"kind": kind, "direction": L.KINDS[kind], "amount_crore": amount, "status": status, "evidence_quote": quote,
               "confidence": 0.9}
        return L.validate_event(raw, art)

    def test_a_bangla_request_reported_as_a_payment_is_rejected(self):
        ev, why = self._ev("পেট্রোবাংলা অর্থ বিভাগের কাছে ২৭ হাজার কোটি টাকা ভর্তুকি চেয়েছে এই সপ্তাহে।", 27000)
        assert ev is None and "request" in why[0]

    def test_a_bangla_payment_is_kept_with_its_amount_re_read_from_the_quote(self):
        ev, why = self._ev("অর্থ বিভাগ পেট্রোবাংলাকে ৩ হাজার কোটি টাকা ভর্তুকি ছাড় করেছে এই সপ্তাহে।", 3000)
        assert ev and ev["amount_crore"] == 3000.0 and not ev["amount_unverified"]

    def test_a_model_amount_that_is_not_in_the_bangla_quote_is_dropped(self):
        ev, why = self._ev("অর্থ বিভাগ পেট্রোবাংলাকে ৩ হাজার কোটি টাকা ভর্তুকি ছাড় করেছে এই সপ্তাহে।", 30000)
        assert ev and ev["amount_crore"] is None and ev["amount_unverified"]

    def test_a_bangla_future_announcement_is_announced_not_disbursed(self):
        ev, why = self._ev("অর্থ বিভাগ পেট্রোবাংলাকে ৩ হাজার কোটি টাকা ভর্তুকি ছাড় করবে আগামী সপ্তাহে।", 3000)
        assert ev and ev["status"] == "ANNOUNCED"

    def test_a_bangla_running_total_is_rejected(self):
        ev, why = self._ev("এ পর্যন্ত ৭৬ হাজার কোটি টাকা ভর্তুকি দেওয়া হয়েছে বলে জানিয়েছে অর্থ বিভাগ।", 76000)
        assert ev is None and "running total" in why[0]


class TestTheKeywordGateSeesBangla:
    def test_a_bangla_liquidity_story_passes_the_prefilter(self):
        """Without Bangla words the gate scored every Bangla article 0 and skipped it."""
        score, passes = L.keyword_score("বাংলাদেশ ব্যাংক", "অর্থ বিভাগ ভর্তুকি বাবদ ৩ হাজার কোটি টাকা ছাড় করেছে")
        assert passes and score > 3

    def test_a_bangla_sports_story_does_not(self):
        assert not L.keyword_score("ক্রিকেট", "টাইগারদের জয়")[1]


class TestTheRuleReaderLeavesBanglaForAnAiReader:
    def test_without_an_ai_reader_a_bangla_article_waits_it_is_not_read_as_empty(self):
        """The rule reader has no Bangla: it would mark the article 'read' having found nothing, and the story would be
        lost. It waits as 'awaiting_ai', and the backlog alarm says so if no AI reader arrives."""
        s = _mem()
        s.add(NewsArticle(url="https://x/bn1", source="prothomalo_bn_business", title="বাংলাদেশ ব্যাংক ভর্তুকি",
                          summary="", body_text="অর্থ বিভাগ ভর্তুকি বাবদ ৩ হাজার কোটি টাকা ছাড় করেছে বাংলাদেশ ব্যাংকের মাধ্যমে।",
                          fetched_utc=NOW, published_utc=NOW, triage_status="untriaged"))
        s.commit()
        stats = T.triage_pending(s, None, NOW, fetch_body=lambda u: None)
        a = s.query(NewsArticle).one()
        assert a.triage_status == "awaiting_ai" and stats["awaiting_ai"] == 1 and stats["extracted"] == 0
        # and a later AI reader picks it up again: awaiting_ai is part of the pending set
        pend = s.query(NewsArticle).filter(NewsArticle.triage_status.in_(("untriaged", "error", "awaiting_ai"))).count()
        assert pend == 1
