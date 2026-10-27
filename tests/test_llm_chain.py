"""Two models, always: a strong one through the local proxy, and Google AI Studio from anywhere.

The proxy is on the desk's PC, so it is gone when the PC is off - exactly when GitHub's runners do
the reading. The AI Studio key works from there. A chain tries them in order and the first that
answers wins; only if both fail does the rule reader take the article.
"""
import datetime
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, NewsArticle
from engines import llm_provider as P
from engines import news_local as NLoc
from engines import news_rules as R
from engines import news_triage as T

NOW = datetime.datetime(2026, 10, 27, 12)
LNG = ("The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla for the month of "
       "September, officials said. The money will be used to pay import bills.")
EV = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 4000.0, "is_cumulative": False,
      "event_date": None, "announced_date": None, "status": "DISBURSED", "channel": "CASH",
      "counterparty": "Petrobangla", "summary": "s", "confidence": 0.8,
      "evidence_quote": "The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla"}


class Fake:
    """A provider double: answers with `events`, or raises `boom`."""

    def __init__(self, model, events=None, boom=None):
        self.model, self.events, self.boom, self.calls = model, events, boom, 0

    @property
    def model_tag(self):
        return f"llm:{self.model}"

    def chat_json(self, system, user, schema, max_tokens=3000):
        self.calls += 1
        if self.boom:
            raise self.boom
        return {"events": self.events if self.events is not None else [EV]}


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _article(s):
    s.add(NewsArticle(url="https://x/1", source="tbs", title="Govt releases Tk 4,000 crore LNG subsidy", summary="",
                      body_text=LNG, published_utc=datetime.datetime(2026, 10, 20), fetched_utc=NOW,
                      triage_status="untriaged"))
    s.commit()


class TestConfiguration:
    ENV = {"LLM_BASE_URL": "http://127.0.0.1:8317/v1", "LLM_API_KEY": "a", "LLM_MODEL": "gemini-3.8-flash-high",
           "LLM2_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai", "LLM2_API_KEY": "b",
           "LLM2_MODEL": "gemini-3.8-flash", "LLM2_REASONING": "high"}

    def test_two_complete_slots_make_a_chain_in_order(self):
        c = P.from_env(self.ENV)
        assert isinstance(c, P.ChainClient)
        assert [p.model for p in c.providers] == ["gemini-3.8-flash-high", "gemini-3.8-flash"]
        assert c.providers[1].reasoning == "high" and c.providers[0].reasoning is None

    def test_one_slot_is_just_that_provider(self):
        env = {k: v for k, v in self.ENV.items() if k.startswith("LLM2_")}
        c = P.from_env(env)
        assert isinstance(c, P.OpenAICompatClient) and c.model == "gemini-3.8-flash"

    def test_an_incomplete_slot_is_ignored_not_half_used(self):
        env = dict(self.ENV)
        del env["LLM_API_KEY"]
        assert [p.model for p in P.from_env(env).providers] if isinstance(P.from_env(env), P.ChainClient) \
            else P.from_env(env).model == "gemini-3.8-flash"

    def test_nothing_configured_is_none(self):
        assert P.from_env({}) is None

    def test_reasoning_effort_reaches_the_request(self, monkeypatch):
        import requests
        seen = {}

        def post(url, **kw):
            seen.update(kw["json"])
            return type("R", (), {"status_code": 200, "text": "", "json": lambda s: {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}})()
        monkeypatch.setattr(requests, "post", post)
        P.OpenAICompatClient("https://a/v1", "k", "m", reasoning="high").chat_json("s", "u", {})
        assert seen["reasoning_effort"] == "high"
        seen.clear()
        P.OpenAICompatClient("https://a/v1", "k", "m").chat_json("s", "u", {})
        assert "reasoning_effort" not in seen

    def test_the_local_runner_reads_the_second_slot_from_env_too(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("LLM2_API_KEY=zzz\nDATABASE_URL=secret\n", encoding="utf-8")
        env = {}
        NLoc.load_llm_settings(str(f), env)
        assert env == {"LLM2_API_KEY": "zzz"}


class TestTheChain:
    def test_the_first_provider_answers_and_the_second_is_not_called(self):
        a, b = Fake("proxy"), Fake("studio")
        c = P.ChainClient([a, b])
        c.chat_json("s", "u", {})
        assert (a.calls, b.calls) == (1, 0) and c.model_tag == "llm:proxy"

    def test_a_dead_proxy_hands_over_to_ai_studio(self):
        """PC off: the proxy refuses the connection and the next provider reads the article."""
        a, b = Fake("proxy", boom=T.TriageError("proxy: ConnectionError")), Fake("studio")
        c = P.ChainClient([a, b])
        assert c.chat_json("s", "u", {}) == {"events": [EV]}
        assert c.model_tag == "llm:studio" and c.failures[0][0] == "proxy"

    def test_every_provider_failing_raises_and_names_each_reason(self):
        c = P.ChainClient([Fake("proxy", boom=T.TriageError("down")), Fake("studio", boom=T.TriageError("429"))])
        with pytest.raises(T.TriageError) as e:
            c.chat_json("s", "u", {})
        assert "proxy: down" in str(e.value) and "studio: 429" in str(e.value)


class TestInTheTriageLoop:
    def test_the_model_that_actually_answered_is_recorded(self):
        s = _mem()
        _article(s)
        chain = P.ChainClient([Fake("proxy", boom=T.TriageError("down")), Fake("studio")])
        st = T.triage_pending(s, chain, NOW, fetch_body=lambda u: None)
        assert st["extracted"] == 1 and st["errors"] == 0 and st["mode"] == "llm"
        assert s.query(NewsArticle).one().triage_model == "llm:studio"
        assert s.query(LiquidityEvent).one().verified_by == "llm"

    def test_when_both_fail_the_rule_reader_still_reads_it_and_the_failure_is_counted(self):
        s = _mem()
        _article(s)
        chain = P.ChainClient([Fake("proxy", boom=T.TriageError("down")), Fake("studio", boom=T.TriageError("429"))])
        st = T.triage_pending(s, chain, NOW, fetch_body=lambda u: None)
        assert st["errors"] == 1 and st["llm_fallbacks"] == 1
        a = s.query(NewsArticle).one()
        assert a.triage_model == R.RULES_MODEL and s.query(LiquidityEvent).one().verified_by == "rules"


class TestSeveralModelsOnOneEndpoint:
    def test_a_comma_list_becomes_a_chain_newest_first(self):
        env = {"LLM_BASE_URL": "https://g/v1", "LLM_API_KEY": "k", "LLM_MODEL": "gemini-3.8-flash, gemini-3.5-flash,gemini-3-flash-preview"}
        c = P.from_env(env)
        assert [p.model for p in c.providers] == ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3-flash-preview"]
        assert all(p.base_url == "https://g/v1" and p.timeout == 45 for p in c.providers)

    def test_the_timeout_is_configurable_and_bad_values_fall_back(self):
        base = {"LLM_BASE_URL": "u", "LLM_API_KEY": "k", "LLM_MODEL": "m"}
        assert P.from_env({**base, "LLM_TIMEOUT": "20"}).timeout == 20
        assert P.from_env({**base, "LLM_TIMEOUT": "soon"}).timeout == 45

    def test_a_slot_can_mix_with_the_proxy_slot(self):
        env = {"LLM_BASE_URL": "http://127.0.0.1:8317/v1", "LLM_API_KEY": "a", "LLM_MODEL": "gemini-3.8-flash-high",
               "LLM2_BASE_URL": "https://g/v1", "LLM2_API_KEY": "b", "LLM2_MODEL": "gemini-3.5-flash,gemini-3-flash-preview"}
        assert [p.model for p in P.from_env(env).providers] == [
            "gemini-3.8-flash-high", "gemini-3.5-flash", "gemini-3-flash-preview"]


class TestADeadProviderIsNotWaitedOnTwice:
    def test_after_one_failure_it_is_skipped_for_the_rest_of_the_run(self):
        """gemini-3.8-flash hangs for the whole timeout. Fifteen articles must not each wait for it."""
        slow, ok = Fake("slow", boom=T.TriageError("ReadTimeout")), Fake("ok")
        c = P.ChainClient([slow, ok])
        for _ in range(5):
            c.chat_json("s", "u", {})
        assert slow.calls == 1 and ok.calls == 5

    def test_if_every_provider_has_failed_they_are_all_tried_again(self):
        a, b = Fake("a", boom=T.TriageError("x")), Fake("b", boom=T.TriageError("y"))
        c = P.ChainClient([a, b])
        for _ in range(2):
            with pytest.raises(T.TriageError):
                c.chat_json("s", "u", {})
        assert (a.calls, b.calls) == (2, 2)

    def test_a_provider_that_recovers_after_everything_failed_is_used(self):
        a = Fake("a", boom=T.TriageError("503"))
        c = P.ChainClient([a])
        with pytest.raises(T.TriageError):
            c.chat_json("s", "u", {})
        a.boom = None
        assert c.chat_json("s", "u", {}) == {"events": [EV]}


class TestARestatedStockIsNotAnEvent:
    """Both strong models, in the first benchmark, reported 'As of early June BB had provided
    Tk 76,000 crore as liquidity support' as a new event. It is a stock built up over years."""

    ART = {"title": "t", "published_utc": datetime.datetime(2026, 10, 25),
           "body_text": "As of early June 2026, BB had provided Tk 76,000 crore as liquidity support to weak banks, the finance minister said."}

    def test_it_is_rejected_whoever_reports_it(self):
        from engines import liquidity_news as L
        ev, why = L.validate_event({**EV, "kind": "BB_BANK_SUPPORT", "amount_crore": 76000.0, "channel": "BB_CREDIT",
            "evidence_quote": "As of early June 2026, BB had provided Tk 76,000 crore as liquidity support to weak banks"}, self.ART)
        assert ev is None and "running total" in why[0]

    def test_a_period_total_is_still_kept_as_cumulative(self):
        from engines import liquidity_news as L
        art = {"title": "t", "published_utc": datetime.datetime(2026, 8, 20),
               "body_text": "Until mid-August the Finance Division had disbursed Tk 47 billion in favour of Energy Division for LNG subsidy."}
        ev, _ = L.validate_event({**EV, "amount_crore": 4700.0, "is_cumulative": True,
            "evidence_quote": "Until mid-August the Finance Division had disbursed Tk 47 billion in favour of Energy Division for LNG subsidy"}, art)
        assert ev is not None and ev["is_cumulative"]
