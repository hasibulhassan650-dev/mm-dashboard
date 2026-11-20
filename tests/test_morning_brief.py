"""The liquidity brief: a desk trades on it, so no number in it is ever typed by a model.

The facts become slots written by code; a model may only draft the connecting sentences as a template of {slot} placeholders, and the template is
refused if it contains a digit, a number word, a direction word, an unknown slot or the wrong number of sentences. A brief is tied to the exact
facts it describes and is served only while they still hold.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
import insights_logic as I
from db import MorningBrief
from engines import morning_brief as M

NOW = datetime.datetime(2026, 11, 19, 10, 0)
FACTS = {"date": "2026-11-19", "regime": "SURPLUS", "segmented": True, "call_pct": 9.22, "call_provisional": False, "call_date": "2026-11-19",
         "repo_pct": 9.5, "slf_pct": 11.0, "sdf_pct": 7.5, "fresh_date": "2026-11-18", "fresh_injection_crore": 1827.0, "fresh_sdf_crore": 2820.0,
         "net_lending_crore": 57652.0, "net_lending_change_crore": 7088.0, "call_change_bp": 29.0,
         "movers": [{"instrument": "CB_REPO", "change_crore": 4651.0}, {"instrument": "SDF", "change_crore": -1203.0}],
         "securities_week_crore": 3068.0, "fx_week_crore": -2497.0,
         "drain_days": [{"date": "2026-11-25", "weekday": "Wed", "net_crore": -4648.0}],
         "omo_known_to": "2026-11-18", "auction_calendar_to": "2026-12-28", "news_events": 4}
GOOD = ("Liquidity is {regime}, which is the first thing to know. Banks are {call} and {call_vs_repo}. {lending}. {securities_week}. "
        "Looking ahead, {drains}.")


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


class TestEveryFigureIsWrittenByCode:
    def test_slots_carry_their_own_numbers_and_directions_from_the_facts(self):
        s = M.slots_from_facts(FACTS)
        assert s["regime"] == "in surplus" and s["call"] == "overnight call money at 9.22%"
        assert s["call_vs_repo"] == "28 basis points below the 9.50% repo rate"
        assert s["lending"] == "BB's net lending stands at 57,652 crore, up 7,088 crore on a week earlier"
        assert s["fx_week"] == "BB's dollar operations drained 2,497 crore of taka over the week", "a sale (negative) is a drain"
        assert "CB Repo (up 4,651 crore)" in s["movers"] and "SDF (down 1,203 crore)" in s["movers"]
        assert s["drains"] == "the heaviest scheduled securities drain is Wednesday 25 November (4,648 crore)"

    def test_a_missing_fact_leaves_no_slot_and_never_an_invented_one(self):
        s = M.slots_from_facts({**FACTS, "call_pct": None, "net_lending_crore": None, "movers": [], "fresh_injection_crore": None})
        assert "call" not in s and "call_vs_repo" not in s and "lending" not in s and "movers" not in s and "fresh" not in s

    def test_an_unsummable_week_says_so_instead_of_a_number(self):
        assert "could not be summed" in M.slots_from_facts({**FACTS, "securities_week_crore": None})["securities_week"]
        assert "no BB dollar auction" in M.slots_from_facts({**FACTS, "fx_week_crore": None})["fx_week"]

    def test_a_provisional_call_print_is_labelled(self):
        assert "(provisional)" in M.slots_from_facts({**FACTS, "call_provisional": True})["call"]


class TestAModelsTemplateIsRefusedUnlessItIsClean:
    SLOTS = M.slots_from_facts(FACTS)

    def test_a_clean_template_passes(self):
        assert M.validate_template(GOOD, self.SLOTS) == []

    def test_a_raw_number_is_rejected_ascii_or_bangla(self):
        assert any("digit" in w for w in M.validate_template(GOOD.replace("first thing", "1 thing"), self.SLOTS))
        assert any("digit" in w for w in M.validate_template(GOOD.replace("first thing", "১ thing"), self.SLOTS))

    def test_a_number_word_is_rejected(self):
        assert any("number word" in w for w in M.validate_template(GOOD.replace("the first thing", "three crore"), self.SLOTS))

    def test_a_direction_word_is_rejected_because_the_slots_already_carry_it(self):
        """'call money fell {call_week}' with a slot that says 'rose' would contradict itself."""
        for word in ("fell", "rose", "higher", "increased", "widening"):
            assert any("direction" in w for w in M.validate_template(GOOD.replace("Looking ahead", f"Money {word} and"), self.SLOTS)), word

    def test_an_unknown_slot_is_rejected(self):
        assert any("unknown slot" in w for w in M.validate_template(GOOD + " {invented}.", self.SLOTS))

    def test_it_must_state_the_regime_and_be_four_to_six_sentences_with_three_slots(self):
        assert any("regime" in w for w in M.validate_template("Hello. {call} here. {lending}. {drains}. More text.", self.SLOTS))
        assert any("sentences" in w for w in M.validate_template("Liquidity is {regime}. {call}. {lending}.", self.SLOTS))
        assert any("fewer than three" in w for w in M.validate_template("Liquidity is {regime}. Good. Fine. Calm. Steady.", self.SLOTS))

    def test_an_empty_template_is_rejected(self):
        assert M.validate_template("", self.SLOTS) == ["empty template"]


class TestTheFinishedTextIsCheckedAgainstTheFacts:
    def test_a_rendered_template_contains_only_numbers_the_facts_hold(self):
        text = M.render(GOOD, M.slots_from_facts(FACTS))
        assert M.final_check(text, FACTS) == []
        assert text.startswith("Liquidity is in surplus") and "57,652" in text

    def test_a_number_that_is_not_in_the_facts_is_caught(self):
        """The belt and braces: if a slot builder ever wrote a wrong figure, this is where it is caught."""
        assert M.final_check("BB lent 9,999 crore.", FACTS) and not M.final_check("BB lent 1,827 crore.", FACTS)

    def test_a_sentence_that_starts_with_a_slot_is_capitalised(self):
        assert M.render("{lending}. And {call}.", {"lending": "bb lends", "call": "call at 9.22%"}).startswith("Bb lends")


class _Client:
    """A fake model: returns canned templates and counts calls (no network in tests)."""
    def __init__(self, *templates):
        self.templates, self.calls = list(templates), 0

    def chat_json(self, system, user, schema, max_tokens=3000):
        self.calls += 1
        return {"template": self.templates[min(self.calls - 1, len(self.templates) - 1)]}

    @property
    def model_tag(self):                      # a property on the real clients (engines/llm_provider.py)
        return "llm:fake"


class TestARunWritesTheBriefAndKeepsTheRecord:
    def test_without_a_model_the_code_brief_exists_and_is_accepted(self):
        s = _mem()
        out = M.run(s, None, NOW, facts=FACTS)
        s.commit()
        row = s.query(MorningBrief).one()
        assert out["code"] and row.model == "code" and row.status == "accepted" and "57,652" in row.text

    def test_a_good_ai_template_is_accepted_and_preferred_over_the_code_brief(self):
        s = _mem()
        out = M.run(s, _Client(GOOD), NOW, facts=FACTS)
        s.commit()
        assert out["ai"] == "accepted"
        h = I.facts_hash(FACTS)
        served = I.latest_brief(s, h)
        assert served["by"] == "an AI model" and served["text"].startswith("Liquidity is in surplus, which is the first thing")

    def test_a_bad_ai_template_is_stored_rejected_with_its_reason_and_the_code_brief_is_served(self):
        s = _mem()
        out = M.run(s, _Client("Liquidity is {regime} and rose by 5 percent. {call}. {lending}. {drains}."), NOW, facts=FACTS)
        s.commit()
        bad = s.query(MorningBrief).filter(MorningBrief.model != "code").one()
        assert bad.status == "rejected" and "digit" in bad.reject_reason and bad.text is None
        assert out["ai"].startswith("rejected")
        assert I.latest_brief(s, I.facts_hash(FACTS))["by"] == "code", "a rejected draft is never served"

    def test_a_model_failure_is_recorded_and_the_brief_survives(self):
        class Boom:
            def chat_json(self, *a, **k):
                raise RuntimeError("rate limited")
        s = _mem()
        M.run(s, Boom(), NOW, facts=FACTS)
        s.commit()
        assert s.query(MorningBrief).filter_by(model="error").one().reject_reason.startswith("RuntimeError")
        assert I.latest_brief(s, I.facts_hash(FACTS)) is not None

    def test_it_does_not_ask_the_model_again_for_facts_it_already_has_an_ai_brief_for(self):
        s = _mem()
        c = _Client(GOOD)
        M.run(s, c, NOW, facts=FACTS)
        s.commit()
        M.run(s, c, NOW, facts=FACTS)
        assert c.calls == 1

    def test_the_daily_attempt_limit_stops_a_failing_model_from_being_hammered(self):
        s = _mem()
        c = _Client("bad one {regime}")
        for i in range(M.BRIEF_MAX_ATTEMPTS_PER_DAY + 3):
            M.run(s, c, NOW + datetime.timedelta(minutes=31 * i), facts={**FACTS, "call_pct": 9.0 + i / 100})   # facts keep changing
            s.commit()
        assert c.calls == M.BRIEF_MAX_ATTEMPTS_PER_DAY

    def test_a_failing_provider_is_not_retried_every_five_minutes(self):
        """The 5-minute PC task hit Gemini 503s five times in 25 minutes and spent the day's attempts before the outage ended."""
        s = _mem()
        c = _Client("bad one {regime}")
        for i in range(6):
            M.run(s, c, NOW + datetime.timedelta(minutes=5 * i), facts={**FACTS, "call_pct": 9.0 + i / 100})
            s.commit()
        assert c.calls == 1
        M.run(s, c, NOW + datetime.timedelta(minutes=35), facts={**FACTS, "call_pct": 9.5})
        assert c.calls == 2


class TestABriefIsServedOnlyWhileItsFactsHold:
    def test_changed_facts_have_a_different_hash_so_yesterdays_sentence_is_not_shown(self):
        s = _mem()
        M.run(s, None, NOW, facts=FACTS)
        s.commit()
        assert I.latest_brief(s, I.facts_hash(FACTS)) is not None
        moved = {**FACTS, "call_pct": 9.40}
        assert I.facts_hash(moved) != I.facts_hash(FACTS) and I.latest_brief(s, I.facts_hash(moved)) is None

    def test_the_engine_and_the_api_compute_the_same_hash(self):
        """The engine imports insights_logic from api/routers, so they cannot drift apart."""
        assert M._insights().facts_hash is I.facts_hash or M._insights().facts_hash(FACTS) == I.facts_hash(FACTS)

    def test_the_brief_facts_hold_only_a_count_of_news_never_a_press_figure(self):
        block = {"as_of": "2026-11-19", "regime": {"label": "SURPLUS", "inputs": {}}, "drivers": {}, "calendar": [], "knowledge": {},
                 "news": {"events": [{"id": 1, "amount_crore": 99999.0, "summary": "Govt releases Tk 99,999 crore"}]}}
        f = I.brief_facts(block)
        assert f["news_events"] == 1 and "99999" not in str(f) and "99,999" not in str(f)
