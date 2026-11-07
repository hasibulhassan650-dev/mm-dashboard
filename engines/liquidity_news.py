"""Liquidity events in Bangladesh's news that BB's own data does not hold.

BB's tables cover what BB does. They do not cover cash the GOVERNMENT puts into
or draws out of the banks - an LNG subsidy released to Petrobangla, arrears paid,
support to a weak bank, a CRR change - and those move liquidity like an OMO does.

Everything in this module is deterministic. A language model is used (see
news_triage.py) only to READ an article; what it reports is never trusted as
given. It must quote the sentence, the quote must be in the article, and the
amount is re-parsed by code from that quote. A model that invents a figure
produces an event that is rejected, not stored.

Nothing here ever reaches a ladder total. These events are shown, linked and
labelled "from news": cash a desk funds against must come from BB's own record.
"""
import datetime
import json
import re
from typing import Dict, List, Optional, Tuple

# kind -> direction. Fixed on our side: the model chooses a kind, never a direction.
KINDS: Dict[str, str] = {
    # cash INTO the banking system
    "SUBSIDY_RELEASE": "INJECT",      # Finance Division cash for LNG/fuel/power/fertiliser subsidy
    "ARREARS_CASH": "INJECT",         # government dues paid in cash
    "BB_BANK_SUPPORT": "INJECT",      # BB lending to named banks outside the OMO tables
    "BB_REFINANCE": "INJECT",         # refinance / pre-finance schemes
    "GOVT_BORROW_BB": "INJECT",       # ways-and-means, devolvement, BB buying government paper
    "BANK_RECAP": "INJECT",           # government capital injection
    "DEPOSIT_PAYOUT": "INJECT",       # deposit insurance / resolution payouts
    "RESERVE_REQ_CUT": "INJECT",      # CRR or SLR cut
    "BB_FX_BUY": "INJECT",            # BB buys dollars: pays taka into the market
    # cash OUT of the banking system
    "RESERVE_REQ_HIKE": "ABSORB",
    "BB_BILL": "ABSORB",
    "SAVINGS_CERT_NET_SALE": "ABSORB",
    "GOVT_REPAY_BB": "ABSORB",
    "BB_FX_SELL": "ABSORB",
    "TAX_DEADLINE": "ABSORB",
    # shown, never summed
    "SPECIAL_BOND_ARREARS": "NEUTRAL",   # bonds in place of cash; cash only if BB lends against them
    "BUDGET_SUPPORT_FX": "NEUTRAL",      # IMF / World Bank / ADB money arriving in dollars
}
STATUSES = ("ANNOUNCED", "APPROVED", "DISBURSED", "COMPLETED")

# Who read an event. An event read ONLY by the free rule reader is an unconfirmed lead: shown, never added to a total,
# never announced, until a stronger reader reads the same article. The rule reader stored a 3,000 crore release as
# 5,100 and a request as a payment, so it is not trusted to move a number on its own.
READER_RANK = {"rules": 0, "llm": 1, "claude": 1, "research": 2, "human": 3}
CONFIRMED_SQL = "COALESCE(verified_by, 'llm') <> 'rules' AND superseded_utc IS NULL"
CHANNELS = ("CASH", "BONDS", "BB_CREDIT", "REGULATORY")

# Of any size: a CRR change or BB lending to a bank matters whatever its amount.
ALWAYS_MATTERS = {"RESERVE_REQ_CUT", "RESERVE_REQ_HIKE", "GOVT_BORROW_BB",
                  "GOVT_REPAY_BB", "BB_BANK_SUPPORT"}
MATTERS_MIN_CRORE = 500.0
MATTERS_MIN_CONFIDENCE = 0.6

# ── the keyword prefilter: cheap, explicit, and auditable ─────────────────────
_ACTORS = ("finance division", "bangladesh bank", "central bank", "petrobangla", "bpdb", "bpc",
           "bcic", "ministry of finance", "finance ministry", "finance adviser", "finance minister",
           "imf", "world bank", "adb", "power division", "energy division", "bank resolution",
           "deposit insurance", "bb ", "bb's", "treasury")
_ACTIONS = ("subsidy", "subsidies", "released", "release of", "disbursed", "disbursement", "arrears",
            "dues", "special bond", "liquidity support", "liquidity", "printed money", "money printing",
            "print money", "refinance", "pre-finance", "crr", "slr", "cash reserve", "statutory liquidity",
            "ways and means", "devolvement", "bb bill", "savings certificate", "sanchayapatra",
            "recapitali", "deposit insurance", "merged bank", "budget support", "tranche",
            "overdraft", "loan from bangladesh bank", "borrow from bangladesh bank", "bailout",
            "capital injection", "bank resolution")
_MONEY = ("tk", "taka", "crore", "billion", "million", "bdt", "lakh", "$")
_REGULATORY = ("crr", "slr", "cash reserve", "statutory liquidity")


def _has(word: str, low: str) -> bool:
    """Whole-word for short tokens (so 'bpc' is not inside another word), else substring."""
    if len(word) <= 4 and word.isalpha():
        return re.search(rf"\b{re.escape(word)}\b", low) is not None
    return word in low


def keyword_score(title: str, text: str = "") -> Tuple[int, bool]:
    """(score, passes). Passes only with an actor AND an action AND money (or CRR/SLR)."""
    low = f"{title or ''} {text or ''}".lower()
    actors = sum(_has(a, low) for a in _ACTORS)
    actions = sum(_has(a, low) for a in _ACTIONS)
    money = sum(_has(m, low) for m in _MONEY)
    regulatory = any(_has(r, low) for r in _REGULATORY)
    passes = bool(actors and actions and (money or regulatory))
    return actors + 2 * actions + money, passes


# ── amounts, re-read by code ──────────────────────────────────────────────────
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_UNIT = r"(lakh\s+crore|crore|cr\b|trillion|billion|bn\b|million|mn\b|lakh)"
_PRE = re.compile(rf"(?:tk\.?|bdt|taka)\s*{_NUM}[\s-]*{_UNIT}?", re.I)
_POST = re.compile(rf"{_NUM}[\s-]*{_UNIT}\s*(?:taka|tk\b)", re.I)
# 1 lakh = 100,000 = 0.01 crore. (It was 0.1: "Tk 5,000 lakh" read as 500 crore, which is 50.)
_TO_CRORE = {"lakh crore": 100000.0, "crore": 1.0, "cr": 1.0, "trillion": 100000.0,
             "billion": 100.0, "bn": 100.0, "million": 0.1, "mn": 0.1, "lakh": 0.01}


def amounts_crore(text: str) -> List[float]:
    """Every taka amount stated in `text`, in crore. A bare number with no unit is
    ignored: "Tk 4,000" could be a per-unit price, and guessing is how a wrong
    figure gets stored. Dollar amounts are ignored here (they are not taka)."""
    out: List[float] = []
    for rx in (_PRE, _POST):
        for m in rx.finditer(text or ""):
            raw, unit = m.group(1), (m.group(2) or "")
            if not unit:
                continue
            unit = re.sub(r"\s+", " ", unit.lower())
            try:
                v = float(raw.replace(",", ""))
            except ValueError:
                continue
            out.append(round(v * _TO_CRORE[unit], 4))
    return out


def verify_amount(model_amount, quote: str, kind: str = None) -> Tuple[Optional[float], bool]:
    """(amount_crore, unverified). The quote decides, never the model.

    If the model gave an amount, it must match a figure in its own quote within 1%
    - and then the QUOTE's figure is what is stored. If it does not, the amount is
    dropped and flagged. If the model gave none but the quote states exactly one,
    that one is taken: it is read by code, not inferred.

    When the quote states SEVERAL amounts ("released Tk 2,500 crore of the Tk 4,200 crore asked"), a figure merely
    being in the quote is not enough: it must be the one attached to the act (news_rules._amount_for), or the amount
    is dropped and flagged. Passing the kind turns this check on.
    """
    cands = amounts_crore(quote)
    if kind and model_amount is not None and len(set(cands)) > 1:
        from engines import news_rules as _R
        attached = _R._amount_for(quote, kind)
        try:
            m = float(model_amount)
        except (TypeError, ValueError):
            return None, True
        if attached is not None and attached and abs(attached - m) / attached <= 0.01:
            return attached, False
        return None, True
    if model_amount is None:
        return (cands[0], False) if len(set(cands)) == 1 else (None, False)
    try:
        m = float(model_amount)
    except (TypeError, ValueError):
        return None, True
    for c in cands:
        if c and abs(c - m) / c <= 0.01:
            return c, False
    return None, True


# ── validating what the model reported ────────────────────────────────────────
def _norm(s: str) -> str:
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"') \
        .replace("”", '"').replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _date(v) -> Optional[datetime.date]:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def validate_event(raw: Dict, article: Dict) -> Tuple[Optional[Dict], List[str]]:
    """(event | None, reasons). None means rejected; reasons say why or what was degraded."""
    why: List[str] = []
    kind = (raw.get("kind") or "").strip().upper()
    if kind not in KINDS:
        return None, [f"unknown kind '{kind}'"]
    if (raw.get("direction") or "").strip().upper() != KINDS[kind]:
        return None, [f"direction '{raw.get('direction')}' does not match {kind} ({KINDS[kind]})"]

    quote = (raw.get("evidence_quote") or "").strip()
    haystack = _norm(f"{article.get('title') or ''} {article.get('body_text') or article.get('summary') or ''}")
    if len(quote) < 20 or _norm(quote) not in haystack:
        return None, ["the evidence quote is not in the article"]

    # A REQUEST is not an event. The first real Claude call over a live article reported
    # "Petrobangla has requested Tk 27,000 crore" as a subsidy release: the quote was verbatim
    # and the amount matched it, so every other check passed. Only the sentence's meaning was
    # wrong, so the rule reader's own test of meaning is applied to every reader's output: a
    # sentence that asks, estimates or plans, and contains no act of paying, is rejected.
    from engines import news_rules as _R
    done, future = _R._tense(quote)
    if _R.is_request(quote) and not done and not (future and kind in ALWAYS_MATTERS):
        return None, ["the quote is a request, estimate or plan, not a payment"]

    # A running STOCK is not new cash: "as of June BB had provided Tk 76,000 crore" restates what
    # has been lent over years. gemini-3.8-flash-high and gemini-3.5-flash both reported it as an
    # event in the first model benchmark, so the rule reader's test applies to every reader.
    if _R._STOCK.search(quote):
        return None, ["the quote restates a running total ('as of', 'so far', 'outstanding'), not a new payment"]

    amount, unverified = verify_amount(raw.get("amount_crore"), quote, kind)
    if unverified:
        why.append("amount does not match the quote; dropped")

    # With no publication date the window anchors on when WE fetched it, so an undated article cannot carry an
    # event dated years away.
    pub = _date(article.get("published_utc")) or _date(article.get("fetched_utc"))

    def near(v):
        d = _date(v)
        if d is None or pub is None:
            return d if pub is None else None
        return d if pub - datetime.timedelta(days=120) <= d <= pub + datetime.timedelta(days=60) else None

    status = (raw.get("status") or "").upper()
    if future and not done:
        status = "ANNOUNCED"      # "will release": whatever the reader labelled it, no cash has moved
    channel = (raw.get("channel") or "").upper()
    if "confidence" in raw and raw["confidence"] is None:
        conf = None                  # the rule reader states no probability: UNCONFIRMED, not "0.7"
    else:
        try:
            conf = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
    ev = {
        "kind": kind, "direction": KINDS[kind], "amount_crore": amount,
        "amount_unverified": unverified,
        "is_cumulative": bool(raw.get("is_cumulative")),
        "event_date": near(raw.get("event_date")), "announced_date": near(raw.get("announced_date")) or (pub if pub else None),
        "status": status if status in STATUSES else None,
        "channel": channel if channel in CHANNELS else None,
        "counterparty": (raw.get("counterparty") or "").strip()[:120] or None,
        "summary": (raw.get("summary") or "").strip()[:300],
        "evidence_quote": quote[:800], "confidence": conf,
    }
    if raw.get("event_date") and ev["event_date"] is None:
        why.append("event date outside the article's window; dropped")
    return ev, why


def matters(ev: Dict) -> bool:
    """The user's rule: info which matters, not every mention."""
    conf = ev.get("confidence")
    if conf is not None and conf < MATTERS_MIN_CONFIDENCE:
        return False
    if ev["kind"] in ALWAYS_MATTERS:
        return True
    amt = ev.get("amount_crore")
    return amt is not None and amt >= MATTERS_MIN_CRORE


# ── clustering ────────────────────────────────────────────────────────────────
def _cp(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def same_counterparty(a: Optional[str], b: Optional[str]) -> bool:
    a, b = _cp(a), _cp(b)
    if not a and not b:
        return True
    return bool(a and b and (a == b or a in b or b in a))


def dedupe_key(ev: Dict, url: str = "") -> str:
    """Exact identity of one reading. It carries the DAY and the first article, not the month: grouping by month made
    two equal payments to the same party 11 days apart one event. Merging different articles is the job of
    merge_or_create's tested predicate, never of this key."""
    import hashlib
    d = ev.get("event_date") or ev.get("announced_date")
    amt = ev.get("amount_crore")
    # A running total and a single payment of the same size are different facts.
    return (f"{ev['kind']}|{_cp(ev.get('counterparty'))[:40]}|{round(amt) if amt else 'na'}|"
            f"{d.isoformat() if d else 'na'}|{'cum' if ev.get('is_cumulative') else 'one'}|"
            f"{hashlib.sha1((url or '').encode()).hexdigest()[:10]}")


_RANK = {s: i for i, s in enumerate(STATUSES)}


def _src(article: Dict) -> Dict:
    pub = article.get("published_utc")
    return {"url": article.get("url"), "source": article.get("source"), "title": article.get("title"),
            "published": pub.isoformat() if hasattr(pub, "isoformat") else pub}


def _same_event(row, ev: Dict, url: str, anchor) -> bool:
    """Is this stored row the same cash event as the new reading? Tested one rule at a time (Rule 9)."""
    if bool(row.is_cumulative) != bool(ev.get("is_cumulative")):
        return False
    a, b = row.amount_crore, ev.get("amount_crore")
    rd = row.event_date or row.announced_date
    days = abs((rd - anchor).days) if (anchor and rd) else None
    if days is not None and days > 10:
        return False
    both = a is not None and b is not None
    if both and abs(a - b) / max(a, b) > 0.05:
        return False
    ca, cb = _cp(row.counterparty), _cp(ev.get("counterparty"))
    shared_url = url in {s.get("url") for s in json.loads(row.sources_json or "[]")}
    if both:
        if ca and cb:
            return same_counterparty(row.counterparty, ev.get("counterparty"))
        if not ca and not cb:
            return True
        # one side names the counterparty and the other does not: the same event only when the figures agree
        # tightly and the dates are close (the rule reader leaves it empty; the model names it)
        return abs(a - b) / max(a, b) <= 0.01 and days is not None and days <= 3
    # at least one amount is missing: an absent figure must not make two different events one
    if (a is None) != (b is None) or (a is None and b is None):
        if shared_url:
            return True
        return bool(ca and ca == cb) and days is not None and days <= 3
    return False


def merge_or_create(session, ev: Dict, article: Dict, now: datetime.datetime = None,
                    verified_by: str = "llm"):
    """Fold a validated event into the stored ones. Returns (row, 'created'|'merged').

    A reading by a STRONGER reader that merges into a weaker one takes the row over (amount, status, quote,
    counterparty, confidence, verified_by) and confirms it: an AI reading agreeing with a rule-read lead is the
    same event, confirmed, not a second one."""
    from db import LiquidityEvent, LiquidityEventHistory
    now = now or datetime.datetime.utcnow()
    anchor = ev.get("event_date") or ev.get("announced_date")
    url = article.get("url") or ""
    match = None
    for row in session.query(LiquidityEvent).filter(LiquidityEvent.kind == ev["kind"],
                                                     LiquidityEvent.superseded_utc.is_(None)).all():
        if _same_event(row, ev, url, anchor):
            match = row
            break

    if match is None:
        key = dedupe_key(ev, url)
        row = session.query(LiquidityEvent).filter_by(dedupe_key=key).one_or_none()
        if row is not None:
            match = row
    if match is not None:
        srcs = json.loads(match.sources_json or "[]")
        if article.get("url") not in {s.get("url") for s in srcs}:
            srcs.append(_src(article))
            match.sources_json = json.dumps(srcs)
        if READER_RANK.get(verified_by, 1) > READER_RANK.get(match.verified_by or "llm", 1):
            session.add(LiquidityEventHistory(event_id=match.id, changed_utc=now, field="verified_by",
                                              old_value=match.verified_by, new_value=verified_by,
                                              source_url=article.get("url")))
            for f in ("amount_crore", "counterparty", "summary", "evidence_quote", "channel", "confidence"):
                v = ev.get(f)
                if v is not None and v != "":
                    setattr(match, f, v)
            match.amount_unverified = bool(ev.get("amount_unverified"))
            match.is_cumulative = bool(ev.get("is_cumulative"))
            match.verified_by, match.confirmed_utc = verified_by, now
        if ev.get("status") and (match.status is None or _RANK[ev["status"]] > _RANK.get(match.status, -1)):
            session.add(LiquidityEventHistory(event_id=match.id, changed_utc=now, field="status",
                                              old_value=match.status, new_value=ev["status"],
                                              source_url=article.get("url")))
            match.status = ev["status"]
        if match.amount_crore is None and ev.get("amount_crore") is not None:
            session.add(LiquidityEventHistory(event_id=match.id, changed_utc=now, field="amount_crore",
                                              old_value=None, new_value=str(ev["amount_crore"]),
                                              source_url=article.get("url")))
            match.amount_crore = ev["amount_crore"]
        match.updated_utc = now
        match.matters = matters({"kind": match.kind, "confidence": match.confidence,
                                 "amount_crore": match.amount_crore})
        session.flush()
        return match, "merged"

    row = LiquidityEvent(
        dedupe_key=dedupe_key(ev, url), kind=ev["kind"], direction=ev["direction"],
        amount_crore=ev.get("amount_crore"), amount_unverified=ev.get("amount_unverified", False),
        is_cumulative=bool(ev.get("is_cumulative")),
        event_date=ev.get("event_date"), announced_date=ev.get("announced_date"),
        status=ev.get("status"), channel=ev.get("channel"), counterparty=ev.get("counterparty"),
        summary=ev.get("summary"), evidence_quote=ev["evidence_quote"],
        sources_json=json.dumps([_src(article)]), confidence=ev.get("confidence"),
        matters=matters(ev), first_seen_utc=now, updated_utc=now, verified_by=verified_by)
    session.add(row)
    session.flush()
    return row, "created"


def supersede_rule_reads(session, url: str, kept_ids, now: datetime.datetime = None) -> int:
    """After an AI reader has read `url`, retire the rule reader's leads from that article that it did not report.

    By ARTICLE, not by merge: the rule reader's event often has the wrong kind (that was its failure), so it would
    never merge with the model's and would live on beside it, announced and added up twice. A lead that cites other
    articles too only loses this one as a source. Returns how many events were superseded."""
    from db import LiquidityEvent
    now = now or datetime.datetime.utcnow()
    kept_ids = set(kept_ids or ())
    n = 0
    for row in session.query(LiquidityEvent).filter(LiquidityEvent.verified_by == "rules",
                                                     LiquidityEvent.superseded_utc.is_(None)).all():
        if row.id in kept_ids:
            continue
        srcs = json.loads(row.sources_json or "[]")
        if url not in {x.get("url") for x in srcs}:
            continue
        rest = [x for x in srcs if x.get("url") != url]
        if rest:
            row.sources_json = json.dumps(rest)
        else:
            row.superseded_utc = now
            row.superseded_reason = f"an AI reader read {url} and did not report this"
            n += 1
    session.flush()
    return n


# ── redundancy: do we already hold this fact in BB's own data? ────────────────
_OMO_WORDS = {"sls": "SLS", "special liquidity support": "SLS", "iblf": "IBLF", "mls": "MLS",
              "repo": None, "assured repo": "AR", "sdf": "SDF", "slf": "SLF"}


def redundancy(session, ev: Dict, article: Dict) -> Optional[str]:
    """The table that already holds this event, or None. Redundant events are
    hidden by default: the desk does not want the same fact twice."""
    from sqlalchemy import text
    low = _norm(f"{article.get('title') or ''} {ev.get('summary') or ''} {ev.get('evidence_quote') or ''}")
    when = ev.get("event_date") or ev.get("announced_date")
    url = (article.get("url") or "")
    if "bb.org.bd" in url and "press_release" in url:
        return "press_releases"
    if when is None:
        return None

    def within(days):
        return (str(when - datetime.timedelta(days=days)), str(when + datetime.timedelta(days=days)))

    if ev["kind"] in ("BB_FX_BUY", "BB_FX_SELL"):
        lo, hi = within(3)
        n = session.execute(text("SELECT COUNT(*) FROM fx_auction_results WHERE auction_date "
                                 "BETWEEN :a AND :b AND LOWER(auction_type) = :t"),
                            {"a": lo, "b": hi, "t": "buy" if ev["kind"] == "BB_FX_BUY" else "sell"}).scalar()
        if n:
            return "fx_auction_results"
    if "buyback" in low or "buy-back" in low:
        lo, hi = within(10)
        if session.execute(text("SELECT COUNT(*) FROM buyback_events WHERE settlement_date "
                                "BETWEEN :a AND :b"), {"a": lo, "b": hi}).scalar():
            return "buyback_events"
    for word, instr in _OMO_WORDS.items():
        if instr and re.search(rf"\b{re.escape(word)}\b", low):
            lo, hi = within(2)
            rows = session.execute(text(
                "SELECT SUM(accepted_bdt_crore) FROM omo_transactions WHERE instrument = :i "
                "AND transaction_date BETWEEN :a AND :b AND accepted_bdt_crore > 0"),
                {"i": instr, "a": lo, "b": hi}).scalar()
            if rows:
                amt = ev.get("amount_crore")
                if amt is None or abs(float(rows) - amt) / max(float(rows), amt) <= 0.10:
                    return "omo_transactions"
    return None
