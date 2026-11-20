"""The liquidity brief: a few plain sentences on what is happening to liquidity, with every number written by CODE.

Why not let a model write the paragraph: a model that types a figure can get it wrong, and a desk trades on this. So the model is never allowed to
type one. The facts (api/routers/insights_logic.brief_facts) become SLOTS whose text - digits, direction words and all - is written here from the
facts. A model, when one is available, only drafts the connecting sentences as a TEMPLATE with {slot} placeholders; the template is rejected if it
contains any digit, any number word, any direction word (the slots carry their own), an unknown slot, or the wrong number of sentences. A rejected
draft is stored with its reason. With no model - or no valid draft - the deterministic template below is used, so a brief always exists and is
labelled "composed by code".

A brief is tied to the exact facts it describes (facts_hash): the API serves it only while the live facts still hash to it.
"""
import datetime
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

MODEL = "claude-sonnet-5-5"
BRIEF_MAX_ATTEMPTS_PER_DAY = 6
MIN_SENTENCES, MAX_SENTENCES = 4, 6
MAX_TEMPLATE_CHARS = 700
MAX_TEXT_CHARS = 1700

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "routers")


def _insights():
    """api/routers/insights_logic: the ONE definition of the facts and their hash, shared with the API so both always agree."""
    if _API not in sys.path:
        sys.path.append(_API)
    import insights_logic
    return insights_logic


# ── slots: the only place a figure is written ────────────────────────────────────────────────────────────────────
def _c(v: float) -> str:
    return f"{abs(v):,.0f}"


def _updown(v: float, up: str, down: str) -> str:
    return up if v > 0 else down


def _name(instrument: str) -> str:
    """CB_REPO -> 'CB Repo'; IBLF stays IBLF. The label comes from the key: no list of products."""
    if "_" not in instrument:
        return instrument
    head, *rest = instrument.split("_")
    return " ".join([head.upper()] + [w.capitalize() for w in rest])


def _weekday(iso: Optional[str], fallback: str = "") -> str:
    try:
        return datetime.date.fromisoformat(str(iso)[:10]).strftime("%A")
    except ValueError:
        return fallback


def _day(iso: Optional[str]) -> str:
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
        return f"{d.day} {d.strftime('%B')}"
    except ValueError:
        return str(iso)


def slots_from_facts(f: Dict) -> Dict[str, str]:
    """Phrases built from the facts by code. A slot exists only when its facts do: no fact, no slot, so nothing can be invented."""
    s: Dict[str, str] = {}
    label = f.get("regime")
    s["regime"] = {"SURPLUS": "in surplus", "TIGHT": "tight", "BALANCED": "balanced"}.get(label, "not possible to judge today")
    call, repo = f.get("call_pct"), f.get("repo_pct")
    if call is not None:
        s["call"] = f"overnight call money at {call:.2f}%" + (" (provisional)" if f.get("call_provisional") else "")
        if repo is not None:
            gap = round((call - repo) * 100)
            s["call_vs_repo"] = (f"{abs(gap)} basis points {'above' if gap > 0 else 'below' if gap < 0 else 'level with'} the {repo:.2f}% repo rate"
                                 if gap else f"level with the {repo:.2f}% repo rate")
    if f.get("call_change_bp") is not None and abs(f["call_change_bp"]) >= 1:
        s["call_week"] = f"call money {_updown(f['call_change_bp'], 'rose', 'fell')} {abs(f['call_change_bp']):.0f} basis points over the week"
    nl, chg = f.get("net_lending_crore"), f.get("net_lending_change_crore")
    if nl is not None:
        s["lending"] = (f"BB's net lending stands at {_c(nl)} crore" +
                        (f", {_updown(chg, 'up', 'down')} {_c(chg)} crore on a week earlier" if chg else ""))
    if f.get("fresh_injection_crore") is not None and f.get("fresh_sdf_crore") is not None:
        s["fresh"] = (f"on {_day(f.get('fresh_date'))} BB lent {_c(f['fresh_injection_crore'])} crore of fresh money while banks parked "
                      f"{_c(f['fresh_sdf_crore'])} crore at the standing deposit facility")
        if f.get("segmented"):
            s["split"] = "this is a two-speed market: some banks borrow from BB while others hold surplus they cannot lend"
    if f.get("movers"):
        parts = [f"{_name(m['instrument'])} ({_updown(m['change_crore'], 'up', 'down')} {_c(m['change_crore'])} crore)" for m in f["movers"]]
        s["movers"] = ("the largest facility move this week was " if len(parts) == 1 else "the largest facility moves this week were ") + ", ".join(parts)
    sw = f.get("securities_week_crore")
    s["securities_week"] = ("government securities settlements and receipts could not be summed for the week because some days are not confirmed"
                            if sw is None else
                            f"government securities {_updown(sw, 'added', 'drained')} {_c(sw)} crore of cash over the last seven working days")
    fx = f.get("fx_week_crore")
    s["fx_week"] = ("no BB dollar auction is on record for the week" if fx is None else
                    f"BB's dollar operations {_updown(fx, 'added', 'drained')} {_c(fx)} crore of taka over the week")
    drains = f.get("drain_days") or []
    s["drains"] = (("the heaviest scheduled securities drain is " if len(drains) == 1 else "the heaviest scheduled securities drains are ") +
                   " and ".join(f"{_weekday(d['date'], d.get('weekday', ''))} {_day(d['date'])} ({_c(d['net_crore'])} crore)" for d in drains)
                   if drains else "no large government securities drain is scheduled in the next ten working days")
    s["coverage"] = (f"BB's operations are known up to {_day(f.get('omo_known_to'))} and the auction calendar is published up to "
                     f"{_day(f.get('auction_calendar_to'))}; days beyond are unknown, not zero")
    if f.get("news_events"):
        s["news_note"] = f"{f['news_events']} press-reported event{'s are' if f['news_events'] != 1 else ' is'} listed separately and not counted in any figure"
    return s


def _fallback_template(slots: Dict[str, str]) -> str:
    """The deterministic brief: only slots that exist, so a missing fact leaves a gap, never a made-up sentence."""
    parts = ["Liquidity is {regime}" + (", with {call}, {call_vs_repo}" if "call_vs_repo" in slots else
                                        (", with {call}" if "call" in slots else ""))]
    for key in ("call_week", "lending", "fresh", "split", "movers", "securities_week", "fx_week", "drains", "coverage", "news_note"):
        if key in slots:
            parts.append("{" + key + "}")
    # sentences: capitalise each slot at render time (see render())
    return ". ".join(parts) + "."


# ── the guards on a model's template ─────────────────────────────────────────────────────────────────────────────
_DIGIT = re.compile(r"[0-9০-৯]")
_NUMBER_WORDS = re.compile(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|"
                           r"eighty|ninety|hundred|thousand|lakh|crore|million|billion|percent|half|double|triple)\b", re.I)
# the slots carry their own direction ("rose 7,088 crore"); a template that adds "up"/"fell" can contradict them
_DIRECTION = re.compile(r"\b(rose|rise|rising|fell|fall|falling|up|down|higher|lower|increase\w*|decrease\w*|gain\w*|drop\w*|widen\w*|"
                        r"narrow\w*|surge\w*|jump\w*|slump\w*|climb\w*|declin\w*|grew|grow\w*|shrank|shrink\w*)\b", re.I)
_SLOT = re.compile(r"\{([a-z_]+)\}")


def validate_template(template: str, slots: Dict[str, str]) -> List[str]:
    """Reasons a model's template is refused; an empty list means it may be rendered."""
    why: List[str] = []
    t = (template or "").strip()
    if not t:
        return ["empty template"]
    if len(t) > MAX_TEMPLATE_CHARS:
        why.append(f"longer than {MAX_TEMPLATE_CHARS} characters")
    if _DIGIT.search(t):
        why.append("contains a digit: every number must come from a slot")
    m = _NUMBER_WORDS.search(t)
    if m:
        why.append(f"contains the number word '{m.group(0)}'")
    m = _DIRECTION.search(t)
    if m:
        why.append(f"contains the direction word '{m.group(0)}': the slots carry their own direction")
    used = _SLOT.findall(t)
    unknown = sorted({u for u in used if u not in slots})
    if unknown:
        why.append("unknown slot(s): " + ", ".join(unknown))
    if re.sub(r"\{[a-z_]+\}", "", t).count("{") or re.sub(r"\{[a-z_]+\}", "", t).count("}"):
        why.append("stray brace")
    if "regime" not in used:
        why.append("does not state the regime ({regime})")
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", t) if x.strip()]
    if not MIN_SENTENCES <= len(sentences) <= MAX_SENTENCES:
        why.append(f"{len(sentences)} sentences; need {MIN_SENTENCES}-{MAX_SENTENCES}")
    if len(set(used)) < 3:
        why.append("uses fewer than three distinct slots")
    return why


def render(template: str, slots: Dict[str, str]) -> str:
    """Fill the slots. A sentence that begins with a slot gets its first letter capitalised."""
    def fill(m):
        return slots[m.group(1)]
    out = _SLOT.sub(fill, template)
    sentences = re.split(r"(?<=[.!?])\s+", out.strip())
    return " ".join(x[:1].upper() + x[1:] if x else x for x in sentences)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> List[float]:
    return [float(x.replace(",", "")) for x in _NUM.findall(text or "")]


def final_check(text: str, facts: Dict) -> List[str]:
    """Every number in the finished text must be one the facts contain (absolute value, as the slots state direction in words).
    Dates are compared by their day number. A belt-and-braces test: the slots are written from the facts, so this fails only if a
    slot builder or a template guard has a bug - exactly when it must."""
    allowed = set()

    def collect(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            allowed.add(round(abs(float(v)), 2))
            allowed.add(float(round(abs(float(v)))))
        elif isinstance(v, str):
            for n in re.findall(r"\d+", v):
                allowed.add(float(n))
        elif isinstance(v, dict):
            for x in v.values():
                collect(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                collect(x)
    collect(facts)
    allowed.update({0.0, 7.0, 10.0})                          # "seven working days", "ten working days": fixed windows of the view
    # basis points are the rate gap x 100 and the call move is already in bp; derive the gap the same way the slot does
    if facts.get("call_pct") is not None and facts.get("repo_pct") is not None:
        allowed.add(float(abs(round((facts["call_pct"] - facts["repo_pct"]) * 100))))
    bad = [n for n in numbers_in(text) if round(abs(n), 2) not in allowed and float(round(abs(n))) not in allowed]
    return [f"the text states {n:g}, which is not in the facts" for n in bad]


# ── the model's part ─────────────────────────────────────────────────────────────────────────────────────────────
SYSTEM = (
    "You write the connecting sentences of a short liquidity brief for a Bangladesh money-market desk. You are given SLOTS: named phrases "
    "that already contain every number and direction. Write 4 to 6 sentences that use the slots as {slot_name} placeholders. HARD RULES: "
    "(1) never type a digit or a number word; (2) never type a direction word (up, down, rose, fell, higher, lower, increased, decreased, widened) - "
    "the slots already say it; (3) use only the slot names given, each as {name}; (4) the first sentence must contain {regime}; "
    "(5) plain, direct language, no advice, no forecasts, no new facts. Reply with JSON: {\"template\": \"...\"}.")
SCHEMA = {"type": "object", "properties": {"template": {"type": "string"}}, "required": ["template"], "additionalProperties": False}


def draft(client, slots: Dict[str, str]) -> Tuple[str, str]:
    """(template, model tag) from the language model, or raises. The model sees the slots' text so its sentences connect sensibly,
    but it may only reference them by name."""
    from engines import news_triage as T
    user = "SLOTS (name: what it says):\n" + "\n".join(f"- {k}: {v}" for k, v in slots.items())
    resp = T._ask(client, MODEL, SYSTEM, user, SCHEMA, "low", 1200)
    return str(resp.get("template") or ""), T._model_tag(client, MODEL)


# ── one pass ─────────────────────────────────────────────────────────────────────────────────────────────────────
def run(session, client=None, now: Optional[datetime.datetime] = None, facts: Optional[Dict] = None) -> Dict:
    """Make sure a brief exists for the CURRENT facts: the code brief first (always), then one AI draft if a model is available, the
    facts changed, and the day's attempts are not spent. Returns what happened; never raises into the caller."""
    from db import MorningBrief
    now = now or datetime.datetime.utcnow()
    I = _insights()
    out = {"hash": None, "code": False, "ai": None, "reasons": []}
    if facts is None:
        block = I.insights_block(session)
        facts = I.brief_facts(block)
    h = I.facts_hash(facts)
    out["hash"] = h[:10]
    today = I.dhaka_today(now)
    slots = slots_from_facts(facts)
    base = session.query(MorningBrief).filter_by(facts_hash=h)
    if not base.filter_by(model="code", status="accepted").count():
        template = _fallback_template(slots)
        text = render(template, slots)
        bad = final_check(text, facts)
        session.add(MorningBrief(brief_date=today, facts_hash=h, facts_json=json.dumps(facts, default=str), model="code",
                                 template=template, text=text, status="rejected" if bad else "accepted",
                                 reject_reason="; ".join(bad) or None, generated_utc=now))
        out["code"] = not bad
        out["reasons"] += bad
    if client is None:
        return out
    if base.filter(MorningBrief.model != "code", MorningBrief.status == "accepted").count():
        out["ai"] = "already have an AI brief for these facts"
        return out
    attempts = session.query(MorningBrief).filter(MorningBrief.brief_date == today, MorningBrief.model != "code").count()
    if attempts >= BRIEF_MAX_ATTEMPTS_PER_DAY:
        out["ai"] = f"daily limit of {BRIEF_MAX_ATTEMPTS_PER_DAY} AI attempts reached"
        return out
    try:
        template, tag = draft(client, slots)
    except Exception as exc:
        session.add(MorningBrief(brief_date=today, facts_hash=h, facts_json=None, model="error", template=None, text=None,
                                 status="rejected", reject_reason=f"{type(exc).__name__}: {str(exc)[:300]}", generated_utc=now))
        out["ai"] = f"model call failed: {type(exc).__name__}"
        return out
    why = validate_template(template, slots)
    text = render(template, slots) if not why else None
    if text is not None:
        why += final_check(text, facts)
        if len(text) > MAX_TEXT_CHARS:
            why.append(f"rendered text is {len(text)} characters")
    session.add(MorningBrief(brief_date=today, facts_hash=h, facts_json=None, model=tag, template=template,
                             text=None if why else text, status="rejected" if why else "accepted",
                             reject_reason="; ".join(why) or None, generated_utc=now))
    out["ai"] = "accepted" if not why else "rejected: " + "; ".join(why)
    return out


def main(argv=None) -> int:
    """Used by the PC task (engines/news_local.py) after the news pass: it has the LLM keys."""
    from db import get_session, init_db
    init_db()
    from engines import news_triage as T
    s = get_session()
    try:
        res = run(s, T.get_client())
        s.commit()
        print(json.dumps(res))
        return 0
    except Exception as exc:                      # the brief never fails the news job
        s.rollback()
        print(f"morning brief not written: {type(exc).__name__}: {exc}")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
