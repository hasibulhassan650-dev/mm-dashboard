"""A free language model for the Liquidity Watch, without trusting it any further than Claude.

The desk has no budget for an API, so the watch can use any OpenAI-compatible endpoint that has a
free tier. Free tiers fail routinely and the models are smaller. These tests pin what keeps that
safe: its output goes through the same checks as Claude's, a failing call never leaves an article
unread (the rule reader takes over), and the failure is still counted so a bad key or model name
gets fixed instead of hidden.
"""
import datetime
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import LiquidityEvent, NewsArticle
from engines import llm_provider as P
from engines import news_rules as R
from engines import news_triage as T
import news_logic as NL

NOW = datetime.datetime(2026, 10, 25, 12, 0, 0)
LNG = ("The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla for the month of "
       "September, officials said. The money will be used to pay import bills.")


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _article(s, url="https://x.example/lng"):
    s.add(NewsArticle(url=url, source="tbs", title="Govt releases Tk 4,000 crore LNG subsidy", summary="",
                      body_text=LNG, published_utc=datetime.datetime(2026, 10, 20, 8), fetched_utc=NOW,
                      triage_status="untriaged"))
    s.commit()


def _event(**kw):
    base = {"kind": "SUBSIDY_RELEASE", "direction": "INJECT", "amount_crore": 4000.0, "is_cumulative": False,
            "event_date": "2026-09-20", "announced_date": None, "status": "DISBURSED", "channel": "CASH",
            "counterparty": "Petrobangla", "summary": "LNG subsidy released",
            "evidence_quote": "The Finance Division has released Tk 4,000 crore as LNG subsidy to Petrobangla",
            "confidence": 0.8}
    base.update(kw)
    return base


class FakeFree:
    """Stands in for OpenAICompatClient without any network."""
    label, skip_screen, model_tag = "llm", True, "llm:fake-model"

    def __init__(self, events=None, boom=None):
        self.events, self.boom, self.calls = events, boom, 0

    def chat_json(self, system, user, schema, max_tokens=3000):
        self.calls += 1
        if self.boom:
            raise self.boom
        return {"events": self.events if self.events is not None else [_event()]}


class TestParsingWhatAFreeModelSays:
    def test_plain_json(self):
        assert P.parse_json_object('{"events": []}') == {"events": []}

    def test_a_json_fence(self):
        assert P.parse_json_object('```json\n{"events": [1]}\n```') == {"events": [1]}

    def test_a_sentence_around_the_object(self):
        assert P.parse_json_object('Sure! Here it is:\n{"events": []}\nHope that helps.') == {"events": []}

    @pytest.mark.parametrize("bad", ["", "no json here", "[1, 2, 3]", "{broken"])
    def test_garbage_is_an_error_not_an_event(self, bad):
        with pytest.raises(T.TriageError):
            P.parse_json_object(bad)


class TestTheHttpCall:
    def _post(self, monkeypatch, status=200, body=None, text="", boom=None):
        import requests

        def fake(url, **kw):
            fake.kw = kw
            fake.url = url
            if boom:
                raise boom
            return NS(status_code=status, text=text or json.dumps(body or {}), json=lambda: body)
        monkeypatch.setattr(requests, "post", fake)
        return fake

    def _client(self):
        return P.OpenAICompatClient("https://api.example/v1/", "k", "m1")

    def test_a_good_reply_is_parsed_and_the_request_is_shaped_for_a_free_tier(self, monkeypatch):
        f = self._post(monkeypatch, body={"choices": [{"message": {"content": '{"events": []}'}, "finish_reason": "stop"}]})
        out = self._client().chat_json("sys", "user", {"type": "object"}, 500)
        assert out == {"events": []}
        assert f.url == "https://api.example/v1/chat/completions"
        assert f.kw["headers"]["Authorization"] == "Bearer k"
        assert f.kw["json"]["temperature"] == 0 and f.kw["json"]["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in f.kw["json"]["messages"][0]["content"]

    def test_a_rate_limit_is_named_as_one(self, monkeypatch):
        self._post(monkeypatch, status=429, body={})
        with pytest.raises(T.TriageError, match="rate limit"):
            self._client().chat_json("s", "u", {})

    def test_a_server_error_carries_its_status(self, monkeypatch):
        self._post(monkeypatch, status=401, text="bad key", body={})
        with pytest.raises(T.TriageError, match="401"):
            self._client().chat_json("s", "u", {})

    def test_a_truncated_reply_is_an_error(self, monkeypatch):
        self._post(monkeypatch, body={"choices": [{"message": {"content": '{"events": ['}, "finish_reason": "length"}]})
        with pytest.raises(T.TriageError, match="truncated"):
            self._client().chat_json("s", "u", {})

    def test_an_unexpected_shape_is_an_error(self, monkeypatch):
        self._post(monkeypatch, body={"nope": 1})
        with pytest.raises(T.TriageError, match="unexpected"):
            self._client().chat_json("s", "u", {})

    def test_a_network_failure_is_an_error(self, monkeypatch):
        import requests
        self._post(monkeypatch, boom=requests.ConnectionError("down"))
        with pytest.raises(T.TriageError):
            self._client().chat_json("s", "u", {})


class TestConfiguration:
    def test_all_three_settings_are_required(self):
        assert P.from_env({"LLM_BASE_URL": "u", "LLM_API_KEY": "k"}) is None
        assert P.from_env({}) is None
        c = P.from_env({"LLM_BASE_URL": "https://a/v1", "LLM_API_KEY": "k", "LLM_MODEL": "gemini-x"})
        assert c.model_tag == "llm:gemini-x"

    def test_claude_wins_when_both_are_configured(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        monkeypatch.setenv("LLM_BASE_URL", "u")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "m")
        monkeypatch.setitem(sys.modules, "anthropic", NS(Anthropic=lambda: NS(messages=NS())))
        assert not hasattr(T.get_client(), "chat_json")

    def test_a_free_model_is_used_when_there_is_no_claude_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("LLM_BASE_URL", "https://a/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "m")
        assert hasattr(T.get_client(), "chat_json")

    def test_with_nothing_configured_it_is_none_and_that_means_the_rule_reader(self, monkeypatch):
        for k in ("ANTHROPIC_API_KEY", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM2_BASE_URL", "LLM2_API_KEY", "LLM2_MODEL"):
            monkeypatch.delenv(k, raising=False)
        assert T.get_client() is None


class TestInTheTriageLoop:
    def test_a_free_model_reads_the_article_and_the_event_is_stored(self):
        s = _mem()
        _article(s)
        c = FakeFree()
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["mode"] == "llm" and st["extracted"] == 1 and c.calls == 1, "no separate screening call"
        e = s.query(LiquidityEvent).one()
        assert e.amount_crore == 4000.0 and e.verified_by == "llm"
        assert s.query(NewsArticle).one().triage_model == "llm:fake-model"

    def test_what_a_small_model_invents_is_rejected_by_the_same_guards(self):
        s = _mem()
        _article(s)
        st = T.triage_pending(s, FakeFree(events=[_event(
            evidence_quote="The IMF disbursed Tk 9,999 crore to the government this morning")]), NOW,
            fetch_body=lambda u: None)
        assert st["events_rejected"] == 1 and s.query(LiquidityEvent).count() == 0

    def test_a_wrong_amount_from_a_small_model_is_dropped(self):
        s = _mem()
        _article(s)
        T.triage_pending(s, FakeFree(events=[_event(amount_crore=40000.0)]), NOW, fetch_body=lambda u: None)
        e = s.query(LiquidityEvent).one()
        assert e.amount_crore is None and e.amount_unverified

    def test_a_failing_call_falls_back_to_the_rule_reader_so_nothing_goes_unread(self):
        """A bad key or an exhausted free quota must not stop the watch reading."""
        s = _mem()
        _article(s)
        st = T.triage_pending(s, FakeFree(boom=T.TriageError("m: free-tier rate limit reached (HTTP 429)")), NOW,
                              fetch_body=lambda u: None)
        a = s.query(NewsArticle).one()
        assert a.triage_status == "extracted" and a.triage_model == R.RULES_MODEL
        assert s.query(LiquidityEvent).one().verified_by == "rules"
        assert st["llm_fallbacks"] == 1

    def test_the_failure_is_still_counted_so_it_is_not_hidden(self):
        s = _mem()
        _article(s)
        st = T.triage_pending(s, FakeFree(boom=T.TriageError("HTTP 401")), NOW, fetch_body=lambda u: None)
        assert st["errors"] == 1 and any("401" in r for r in st["reasons"])

    def test_the_page_names_the_free_model_as_the_reader(self):
        s = _mem()
        _article(s)
        T.triage_pending(s, FakeFree(), NOW, fetch_body=lambda u: None)
        assert NL.news_block(s, today=datetime.date(2026, 10, 25))["health"]["reader"] == "llm"

    def test_the_daily_call_ceiling_still_applies_to_a_free_tier(self, monkeypatch):
        """Free tiers have quotas too; exhausting one should stop, not loop."""
        monkeypatch.setattr(T, "MAX_CALLS_PER_DAY", 0)
        monkeypatch.setattr(T, "MAX_CALLS_PER_DAY_FREE", 0)
        s = _mem()
        _article(s)
        c = FakeFree()
        st = T.triage_pending(s, c, NOW, fetch_body=lambda u: None)
        assert st["budget_hit"] and c.calls == 0


def test_the_workflow_passes_the_three_settings_through():
    wf = open(os.path.join(_ROOT, ".github", "workflows", "news_watch.yml"), encoding="utf-8").read()
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        assert f"{k}: ${{{{ secrets.{k} }}}}" in wf
