"""Reading a news article for liquidity events with plain rules - no model, no cost.

The Liquidity Watch must work with no API key at all, because a key is a bill. This reader
finds the same events the Claude reader looks for, using the patterns Bangladesh's financial
press actually uses ("the Finance Division released Tk 2,500 crore", "drawn from the central
bank", "BB cut the CRR"). It goes through exactly the same checks afterwards
(liquidity_news.validate_event): the quote is a sentence of the article, the amount is re-read
from it, the direction matches the kind.

Because the checks cannot catch a misclassification (the sentence is real, the amount is real,
the KIND may be wrong), these rules are deliberately conservative. They would rather miss an
event than invent one, and each rule exists because a real sentence needed it:

  * a sentence that only REQUESTS, estimates or plans ("Petrobangla sought Tk 27,000 crore",
    "BERC projects Tk 41,000 crore") is not an event and is skipped;
  * a sentence that restates a running STOCK ("as of June BB had provided Tk 76,000 crore")
    is skipped - it is not new cash;
  * a sentence reporting a total over a PERIOD ("disbursed Tk 47 billion until mid-August")
    is kept but marked cumulative, so it is shown and never added to a monthly or FY total;
  * with several amounts in one sentence the amount is the one attached to the verb
    ("released only Tk 2,500 crore" - not the Tk 4,200 crore it was asked for); if that
    cannot be told, the amount is left empty rather than guessed.

Every event this reader finds is UNCONFIRMED (verified_by='rules', no confidence number): it is shown as
a lead, never added to a total and never announced, until an AI reader reads the same article and
confirms it. A regex has no probability, and it is the reader that stored a 3,000 crore subsidy as 5,100.

Claude, when a key exists, reads what these rules cannot: unusual wording, several events in
one article, nuance. The rules are the floor, not the ceiling.
"""
import re
from typing import Dict, List, Optional, Tuple

from engines import liquidity_news as L

RULES_MODEL = "rules-v1"

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“(])|(?<=।)\s+")
# "Tk. 5,000 crore" and "Govt. said" are not sentence ends
_ABBREV = re.compile(r"\b(Tk|Mr|Mrs|Ms|Dr|No|Ltd|Co|Govt|St|Prof)\.(?=\s)")

# The bare infinitives (release, disburse) are NOT here: "requested ... to release Tk 27,000 crore" and "plans to
# release" matched them and were stored as payments. "cut", "raise" and "pay" stay (same word in both tenses),
# and _governed() rejects them when "to", "will" or a request verb sits in front.
_DONE = re.compile(
    r"\b(released|releases|disbursed|disburses|disbursement of|paid|pays|provided|provides|lent|lends|injected|"
    r"injects|given|gives|gave|cleared|clears|settled|extended|extends|drawn|borrowed|borrows|withdrawn|issued|"
    r"issues|reallocated|raised|raises|expanded|increased|cut|cuts|reduced|reduces|lowered|slashed|relaxed|eased|"
    r"hiked|hikes|repaid|repays|repaying|sold|sells|bought|buys|purchased|purchases)\b", re.I)
# a verb preceded (within 3 words) by one of these is an intention, a request or a plan, not an act
_GOVERNOR = {"to", "will", "would", "plan", "plans", "planned", "seek", "seeks", "sought", "ask", "asks", "asked",
             "request", "requests", "requested", "urge", "urges", "urged", "want", "wants", "wanted", "propose",
             "proposed", "proposes", "expect", "expects", "expected", "intend", "intends", "decided", "decides",
             "need", "needs", "needed", "should", "shall", "must", "if"}
_REQUEST = re.compile(
    r"\b(seeks?|sought|asked?|demand(ed|s)?|requests?|requested|requisition|propos\w+|plans? to|"
    r"planned|expected|estimat\w+|project(s|ed|ion)?|target(s|ed)?|likely|"
    r"wants?|wanted|needs?|needed|requires?|required|urges?|urged|calls? for)\b", re.I)
# CASE-SENSITIVE on purpose: the month "May" is not a hedge ("a CRR cut from May 1" was dropped as one)
_MODAL = re.compile(r"\b(may|might|could|would)\b")
_FUTURE = re.compile(r"\b(will|is set to|are set to|going to)\b", re.I)
# a running STOCK, not new cash: skipped outright
_STOCK = re.compile(r"\b(as of|outstanding|so far|to date|cumulative(ly)?)\b", re.I)
# a total over a PERIOD: kept, marked cumulative, never summed
_PERIOD = re.compile(
    r"\b(until|till|in total|a total of|total of|during (these|the|this) \w+( \w+)?( months?| weeks?| quarter| year)?|first quarter|"
    r"first (two|three|four|five|six|\d+) (months?|weeks?)|q[1-4]|this fiscal year|fiscal year|"
    r"since (july|the start)|"
    # "in April" is ONE month's payment; "in July and August" is a total over two months.
    r"in (july|august|september|october|november|december|january|february|march|april|may|june) "
    r"(and|to|through) (july|august|september|october|november|december|january|february|march|april|may|june)\b)",
    re.I)

_KINDS: List[Tuple[str, re.Pattern]] = [
    # order matters: the first rule that matches decides
    ("RESERVE_REQ_CUT", re.compile(r"\b(crr|slr|cash reserve|statutory liquidity)\b(\W+\w+){0,6}?\W+(cut|reduc\w+|lower\w*|slash\w*|relax\w*|eas\w+)\b|"
                                    r"\b(cut|reduc\w+|lower\w*|slash\w*|relax\w*|eas\w+)\b(\W+\w+){0,6}?\W+(crr|slr|cash reserve|statutory liquidity)\b", re.I)),
    ("RESERVE_REQ_HIKE", re.compile(r"\b(crr|slr|cash reserve|statutory liquidity)\b(\W+\w+){0,6}?\W+(rais\w+|hik\w+|increas\w+|tighten\w*)\b|"
                                    r"\b(rais\w+|hik\w+|increas\w+|tighten\w*)\b(\W+\w+){0,6}?\W+(crr|slr|cash reserve|statutory liquidity)\b", re.I)),
    ("BB_REFINANCE", re.compile(r"\b(re-?financ\w+|pre-?financ\w+)\b", re.I)),
    ("BB_BANK_SUPPORT", re.compile(r"\b(special liquidity support|liquidity support|emergency liquidity|"
                                   r"inject\w* (fresh |more )?liquidity|printed money|money printing)\b", re.I)),
    ("GOVT_REPAY_BB", re.compile(r"\b(repaid|repay\w*|paid back)\b.*\b(central bank|bangladesh bank)\b", re.I)),
    ("GOVT_BORROW_BB", re.compile(r"\b(ways and means|overdraft|devolv\w+)\b|"
                                  r"\b(borrow\w*|drawn|draw\w*|taken|took)\b.*\b(from|by)\b.*\b(central bank|bangladesh bank)\b", re.I)),
    ("BB_BILL", re.compile(r"\b(bb|bangladesh bank|central bank) bills?\b", re.I)),
    ("SPECIAL_BOND_ARREARS", re.compile(r"\bspecial bonds?\b", re.I)),
    ("DEPOSIT_PAYOUT", re.compile(r"\b(deposit insurance|deposit payout)\b", re.I)),
    ("SUBSIDY_RELEASE", re.compile(r"\bsubsid\w+\b.*\b(lng|gas|petrobangla|power|bpdb|electric\w*|fuel|bpc|fertili\w+|diesel|oil|energy)\b|"
                                   r"\b(lng|gas|petrobangla|power|bpdb|electric\w*|fuel|bpc|fertili\w+|diesel|oil|energy)\b.*\bsubsid\w+\b", re.I)),
    ("ARREARS_CASH", re.compile(r"\b(arrears|overdue (bills|dues|payments)|dues)\b", re.I)),
]

# GOVT_BORROW_BB needs a government as the borrower: "Banks borrowed Tk 15,000 crore from Bangladesh Bank through
# repo" is the interbank/OMO market, which BB's own tables already hold.
_GOVT_SUBJECT = re.compile(r"\b(government|govt|state|finance division|finance ministry|treasury|ministry of finance)\b", re.I)
_FACILITY = re.compile(r"\b(repo|assured repo|sdf|slf|standing (deposit|lending)|iblf|mls|special repo)\b", re.I)
# Cash topics the HEADLINE may name when the sentence itself does not ("... had disbursed Tk 47 billion in favour of
# Energy Division" never says "subsidy"). Never a reserve-requirement change: a CRR headline turned "inflation eased"
# into a reserve cut.
_HEADLINE_KINDS = {"SUBSIDY_RELEASE", "ARREARS_CASH", "SPECIAL_BOND_ARREARS", "BB_REFINANCE"}
_UNCHANGED = re.compile(r"\b(unchanged|kept|keep|maintain\w*|left|retain\w*|steady|no change|status quo)\b", re.I)

_CHANNEL = {"SUBSIDY_RELEASE": "CASH", "ARREARS_CASH": "CASH", "SPECIAL_BOND_ARREARS": "BONDS",
            "BB_BANK_SUPPORT": "BB_CREDIT", "BB_REFINANCE": "BB_CREDIT", "GOVT_BORROW_BB": "BB_CREDIT",
            "GOVT_REPAY_BB": "BB_CREDIT", "BB_BILL": "BB_CREDIT", "DEPOSIT_PAYOUT": "CASH",
            "RESERVE_REQ_CUT": "REGULATORY", "RESERVE_REQ_HIKE": "REGULATORY"}
_PARTIES = ("Petrobangla", "BPDB", "BPC", "BCIC", "Energy Division", "Power Division", "Finance Division",
            "REB", "Rural Electrification Board")
_BANK = re.compile(r"\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,3} (?:Bank|Banks)(?: (?:Ltd|PLC|Limited)\.?)?)")


def sentences(text: str) -> List[str]:
    flat = _ABBREV.sub(lambda m: m.group(1) + "\u2024", re.sub(r"\s+", " ", text or ""))   # protect the dot
    return [x.strip().replace("\u2024", ".") for x in _SENT.split(flat) if len(x.strip()) > 25]


def _amounts_at(sentence: str) -> List[Tuple[int, int, float]]:
    """(start, end, crore) for every unit-bearing taka amount, in order of appearance."""
    out = []
    for rx in (L._PRE, L._POST):
        for m in rx.finditer(sentence):
            raw, unit = m.group(1), (m.group(2) or "")
            if not unit:
                continue
            unit = re.sub(r"\s+", " ", unit.lower())
            try:
                out.append((m.start(), m.end(), round(float(raw.replace(",", "")) * L._TO_CRORE[unit], 4)))
            except (ValueError, KeyError):
                continue
    return sorted(out)


def _amount_for(sentence: str, kind: str) -> Optional[float]:
    """The amount attached to the act, not merely present in the sentence."""
    amts = _amounts_at(sentence)
    if not amts:
        return None
    if kind in ("GOVT_BORROW_BB", "GOVT_REPAY_BB"):
        anchor = re.search(r"\b(from|by|to) the (central bank|bangladesh bank)\b|\bfrom (central bank|bangladesh bank|bb)\b", sentence, re.I)
        if anchor:
            before = [a for a in amts if a[1] <= anchor.start() + 1]
            if before:
                return before[-1][2]            # nearest amount BEFORE "from the central bank"
    verb = first_done(sentence)
    if verb:
        after = [a for a in amts if a[0] >= verb.end() - 1 and a[0] - verb.end() <= 80]
        if after:
            return after[0][2]                 # "released only Tk 2,500 crore" - not what was asked for
    if len(amts) == 1:
        return amts[0][2]
    return None                                # several amounts, none attached to the act: do not guess


def _counterparty(kind: str, sentence: str) -> Optional[str]:
    if kind in ("SUBSIDY_RELEASE", "ARREARS_CASH", "SPECIAL_BOND_ARREARS"):
        for p in _PARTIES:
            if p.lower() in sentence.lower() and p != "Finance Division":
                return p
    if kind in ("BB_BANK_SUPPORT", "DEPOSIT_PAYOUT"):
        for m in _BANK.finditer(sentence):
            name = m.group(1)
            if not re.match(r"(Bangladesh|Central|The) Bank", name) and name.split()[0] not in ("Bangladesh", "Central"):
                return name
    return None


def _governed(sentence: str, verb: "re.Match") -> bool:
    """True when the verb is an intention, request or plan: 'to', 'will', 'plans', 'requested' ... within the three
    words before it ('Petrobangla requested the Finance Division to release Tk 27,000 crore')."""
    before = re.findall(r"[a-z']+", sentence[:verb.start()].lower())[-3:]
    return any(w in _GOVERNOR for w in before)


def first_done(sentence: str):
    """The first verb that reports an ACT, skipping governed ones; None when there is none."""
    return next((m for m in _DONE.finditer(sentence) if not _governed(sentence, m)), None)


def is_request(sentence: str) -> bool:
    """Asks, estimates or plans. Month names are not hedges: the modal test is case-sensitive."""
    return bool(_REQUEST.search(sentence) or _MODAL.search(sentence))


def _tense(sentence: str) -> Tuple[bool, bool]:
    """(done, future). 'will release' is FUTURE: a future-tense marker before the verb makes it an
    announcement, not a payment. 'had released only Tk 2,500 crore, will release more' is done: the
    verb comes first. A verb governed by 'to' or a request verb is neither."""
    verb, fut = first_done(sentence), _FUTURE.search(sentence)
    done = bool(verb) and not (fut is not None and fut.start() < verb.start())
    return done, fut is not None and not done


def _status(kind: str, sentence: str) -> str:
    done, future = _tense(sentence)
    if future and not done:
        return "ANNOUNCED"
    if re.search(r"\bapproved?\b|\breallocated|\bexpanded|\braised|\bincreased", sentence, re.I) and kind == "BB_REFINANCE":
        return "APPROVED"
    if kind in ("RESERVE_REQ_CUT", "RESERVE_REQ_HIKE"):
        return "COMPLETED"
    return "DISBURSED"


def _kind_of(sentence: str, title: str = "") -> Optional[str]:
    """The kind of THIS sentence: the first pattern that matches it (order matters), with the guards a bare pattern
    cannot hold. Only a cash topic named in the headline may stand in for a kind the sentence does not name, and only
    when the sentence names the party (so an unrelated payment in the same article cannot borrow the headline)."""
    for kind, rx in _KINDS:
        if not rx.search(sentence):
            continue
        if kind == "GOVT_BORROW_BB" and not re.search(r"\b(ways and means|overdraft|devolv\w+)\b", sentence, re.I):
            # the borrower must be a government (the headline may say so) and the sentence must not be the
            # interbank / OMO market, which BB's own tables hold
            if not _GOVT_SUBJECT.search(f"{title} {sentence}") or _FACILITY.search(sentence):
                continue
        return kind
    for kind, rx in _KINDS:
        if kind in _HEADLINE_KINDS and rx.search(title or "") and _counterparty(kind, sentence):
            return kind
    return None


def extract(article: Dict) -> List[Dict]:
    """Raw events in the same shape the Claude reader returns, for validate_event to check."""
    title = article.get("title") or ""
    body = article.get("body_text") or article.get("summary") or ""
    out: List[Dict] = []
    best: Dict[Tuple, Dict] = {}
    for s in sentences(body):
        if _STOCK.search(s):
            continue
        done, future = _tense(s)
        if is_request(s) and not done:
            continue                            # asked for / estimated / planned: not an event
        if not done and not future:
            continue
        # The SENTENCE decides its kind, never the headline: a CRR headline turned "inflation eased" into a reserve
        # cut, and one LNG-subsidy headline turned three unrelated payments into three subsidy releases (5,100 cr
        # for a 3,000 cr release).
        kind = _kind_of(s, title)
        if kind is None:
            continue
        if kind in ("RESERVE_REQ_CUT", "RESERVE_REQ_HIKE") and _UNCHANGED.search(s):
            continue                            # "the CRR was left unchanged"
        # A future "will" is an event only where the announcement itself is the news
        # (a CRR change, BB lending to a bank), never for a subsidy someone is going to ask for.
        if not done and kind not in L.ALWAYS_MATTERS:
            continue
        amount = _amount_for(s, kind)
        # No amount and not an always-matters kind: nothing worth saying.
        if amount is None and kind not in L.ALWAYS_MATTERS:
            continue
        if kind in ("RESERVE_REQ_CUT", "RESERVE_REQ_HIKE"):
            amount = None                       # a ratio change has no taka amount
        cp = _counterparty(kind, s)
        ev = {
            "kind": kind, "direction": L.KINDS[kind], "amount_crore": amount,
            "is_cumulative": bool(_PERIOD.search(s)),
            "event_date": None, "announced_date": None,
            "status": _status(kind, s), "channel": _CHANNEL.get(kind),
            "counterparty": cp,
            "summary": s[:240],
            "evidence_quote": s,
            "confidence": None,                 # a regex has no probability: the event is UNCONFIRMED instead
        }
        # One article, one event per (kind, counterparty): restating the same payment in later sentences is not
        # more cash. The sentence that states an amount for a single payment wins over one that does not.
        key = (kind, L._cp(cp))
        cur = best.get(key)
        if cur is None or (cur["amount_crore"] is None and amount is not None) or \
                (cur["is_cumulative"] and not ev["is_cumulative"] and amount is not None):
            best[key] = ev
    out = list(best.values())
    return out
