"""A free model for the Liquidity Watch: any OpenAI-compatible chat endpoint.

Claude is the best reader but it is a paid API. Several providers offer a free tier through
the same OpenAI-style `/chat/completions` interface, so one small client covers them all:

    LLM_BASE_URL   LLM_API_KEY   LLM_MODEL
    Google Gemini  https://generativelanguage.googleapis.com/v1beta/openai   (AI Studio key)
    Groq           https://api.groq.com/openai/v1
    OpenRouter     https://openrouter.ai/api/v1   (models whose id ends in ":free")
    GitHub Models  https://models.github.ai/inference   (key = a GitHub token with models:read)

Free tiers are rate-limited and the models are smaller, so three things are different from the
Claude path - all deliberate:

  * the model is asked for one JSON object and its reply is parsed defensively (fences, prose);
  * there is no separate screening call: it would burn half the free quota, and the keyword
    filter has already removed most articles;
  * a failing call (bad key, wrong model name, 429 rate limit) does NOT leave the article unread:
    the caller falls back to the free rule reader, so coverage never drops below the floor, and
    the failure is still counted and shown so a misconfiguration is fixed, not hidden.

Whatever the model says is checked by exactly the same code as Claude's output
(liquidity_news.validate_event): the quote must be in the article, the amount is re-read from
it, the direction must match the kind. A smaller model that invents something is rejected there.
"""
import json
import logging
import os
import re
from typing import Dict, Optional

log = logging.getLogger("llm_provider")


class OpenAICompatClient:
    """Marker attributes the triage code looks at; see news_triage._ask."""
    label = "llm"
    skip_screen = True            # no screening call: it would double the free-tier usage

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 90):
        self.base_url, self.api_key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout

    @property
    def model_tag(self) -> str:
        return f"llm:{self.model}"

    def chat_json(self, system: str, user: str, schema: Dict, max_tokens: int = 3000) -> Dict:
        import requests

        from engines.news_triage import TriageError
        payload = {
            "model": self.model, "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system + "\n\nReply with ONE JSON object and nothing else, "
                                               "matching this JSON Schema:\n" + json.dumps(schema)},
                {"role": "user", "content": user}],
        }
        try:
            r = requests.post(f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}",
                                       "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise TriageError(f"{self.model}: {type(exc).__name__}")
        if r.status_code == 429:
            raise TriageError(f"{self.model}: free-tier rate limit reached (HTTP 429)")
        if r.status_code != 200:
            raise TriageError(f"{self.model}: HTTP {r.status_code} {r.text[:160]!r}")
        try:
            choice = r.json()["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise TriageError(f"{self.model}: unexpected response shape")
        if choice.get("finish_reason") == "length":
            raise TriageError(f"{self.model}: reply truncated at {max_tokens} tokens")
        return parse_json_object(text, self.model)


def parse_json_object(text: str, who: str = "model") -> Dict:
    """The first JSON object in a reply, tolerating ```json fences and a sentence around it."""
    from engines.news_triage import TriageError
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        out = json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise TriageError(f"{who}: no JSON object in the reply")
        try:
            out = json.loads(t[a:b + 1])
        except ValueError as exc:
            raise TriageError(f"{who}: unparseable JSON: {exc}")
    if not isinstance(out, dict):
        raise TriageError(f"{who}: the reply was not a JSON object")
    return out


def from_env(env=None) -> Optional[OpenAICompatClient]:
    """A free-tier client if LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are all set, else None."""
    env = os.environ if env is None else env
    base, key, model = env.get("LLM_BASE_URL"), env.get("LLM_API_KEY"), env.get("LLM_MODEL")
    if base and key and model:
        return OpenAICompatClient(base, key, model)
    return None
