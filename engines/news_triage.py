"""Reading the news: fetch, filter, ask Claude, validate, store.

The only judgement here is "does this article report a real liquidity event, and
which one" - classification and extraction, which is what a language model is for.
Everything that decides whether to BELIEVE the answer is code (liquidity_news.py):
the sentence it quotes must be in the article, the amount is re-read from that
sentence, the date must be plausible, the kind and direction must be ones we
defined. A model that misreads or invents produces an event that is rejected.

Two models, because the work is two different sizes:
  * screen  - claude-haiku-5-5: title + the opening of the article, one yes/no
  * extract - claude-sonnet-5-5: the whole article, structured events

Budgets are hard and fail LOUD. With no ANTHROPIC_API_KEY nothing is dropped:
articles are stored as `untriaged` and the digest says so in red.
"""
import datetime
import json
import logging
import os
from typing import Callable, Dict, List, Optional

from engines import liquidity_news as L
from engines import news_rules as R

log = logging.getLogger("news_triage")

SCREEN_MODEL = "claude-haiku-5-5"
EXTRACT_MODEL = "claude-sonnet-5-5"

MAX_BODY_FETCHES = 40
MAX_SCREENS = 40
MAX_EXTRACTS = 15
MAX_CALLS_PER_DAY = 400
SCREEN_CHARS = 1500


class TriageError(RuntimeError):
    """The model call failed in a way that must be retried, not swallowed."""


_KIND_HELP = {
    "SUBSIDY_RELEASE": "Finance Division releases cash for an LNG, fuel, power or fertiliser subsidy (e.g. to Petrobangla, BPDB, BPC, BCIC)",
    "ARREARS_CASH": "government pays overdue bills in cash",
    "BB_BANK_SUPPORT": "Bangladesh Bank lends or 'prints money' to named banks (liquidity support, deposit-payout support)",
    "BB_REFINANCE": "a Bangladesh Bank refinance or pre-finance scheme",
    "GOVT_BORROW_BB": "the government borrows from Bangladesh Bank (ways and means, devolvement, BB buying government paper)",
    "BANK_RECAP": "government capital injected into a bank",
    "DEPOSIT_PAYOUT": "deposit insurance or resolution payouts",
    "RESERVE_REQ_CUT": "CRR or SLR lowered",
    "BB_FX_BUY": "Bangladesh Bank buys dollars from banks",
    "RESERVE_REQ_HIKE": "CRR or SLR raised",
    "BB_BILL": "Bangladesh Bank issues its own bills to mop up cash",
    "SAVINGS_CERT_NET_SALE": "net sales of national savings certificates",
    "GOVT_REPAY_BB": "the government repays Bangladesh Bank",
    "BB_FX_SELL": "Bangladesh Bank sells dollars to banks",
    "TAX_DEADLINE": "a large seasonal tax or dividend payment drains bank cash",
    "SPECIAL_BOND_ARREARS": "bonds issued in place of cash for arrears (neutral unless BB lends against them)",
    "BUDGET_SUPPORT_FX": "IMF / World Bank / ADB money arriving in dollars (neutral)",
}

SCREEN_SYSTEM = (
    "You screen Bangladeshi news for a money-market desk. Answer is_candidate=true ONLY if the "
    "article reports that a specific amount of cash was, or is about to be, released, paid, lent, "
    "borrowed or withdrawn between the government, Bangladesh Bank and the banking system - a subsidy "
    "release, arrears payment, support to a bank, a CRR/SLR change, a refinance scheme, BB or "
    "government borrowing, BB bills, savings-certificate sales, a bank recapitalisation or deposit "
    "payout. General economy commentary, forecasts, stock prices, tax policy debates and repeats of "
    "Bangladesh Bank's own auctions or OMO are NOT candidates.")

EXTRACT_SYSTEM = (
    "You extract liquidity events from one Bangladeshi news article for a money-market desk. "
    "Report ONLY what the article states. Never infer an amount, a date or a status. For every event "
    "give `evidence_quote`: the exact sentence from the article that supports it, copied verbatim. "
    "`amount_crore` is in Bangladeshi crore taka (1 billion taka = 100 crore; 1 million = 0.1 crore; "
    "1 lakh crore = 100,000 crore); null if the article gives no taka amount. `is_cumulative` is true "
    "when the amount is a running total over a period (\"disbursed Tk 47 billion until mid-August\", "
    "\"so far this fiscal year\", \"in the first quarter\") rather than one payment. `status`: ANNOUNCED "
    "(planned or requested), APPROVED, DISBURSED (cash moved) or COMPLETED. `channel`: CASH, BONDS, "
    "BB_CREDIT (Bangladesh Bank lending) or REGULATORY (a reserve-requirement change). `direction` "
    "must match the kind. Dates are YYYY-MM-DD or null. `confidence` is 0-1: how sure you are the "
    "article reports a real, specific event of that kind. Return an empty list if there is none. "
    "Event kinds:\n" + "\n".join(f"- {k} ({L.KINDS[k]}): {v}" for k, v in _KIND_HELP.items()))


def _nullable(t):
    return {"anyOf": [{"type": t}, {"type": "null"}]}


EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["events"],
    "properties": {"events": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "direction", "amount_crore", "is_cumulative", "event_date", "announced_date",
                     "status", "channel", "counterparty", "summary", "evidence_quote", "confidence"],
        "properties": {
            "kind": {"type": "string", "enum": list(L.KINDS)},
            "direction": {"type": "string", "enum": ["INJECT", "ABSORB", "NEUTRAL"]},
            "amount_crore": _nullable("number"), "is_cumulative": {"type": "boolean"},
            "event_date": _nullable("string"),
            "announced_date": _nullable("string"), "status": _nullable("string"),
            "channel": _nullable("string"), "counterparty": _nullable("string"),
            "summary": {"type": "string"}, "evidence_quote": {"type": "string"},
            "confidence": {"type": "number"},
        }}}}}

SCREEN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["is_candidate"],
                 "properties": {"is_candidate": {"type": "boolean"}}}


def get_client():
    """An Anthropic client, or None when there is no key (nothing is dropped then)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def _ask(client, model: str, system: str, user: str, schema: Dict, effort: str,
         max_tokens: int) -> Dict:
    """One structured call. Raises TriageError on a refusal, truncation or bad JSON."""
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}])
    if resp.stop_reason == "refusal":
        raise TriageError(f"{model} declined the request")
    if resp.stop_reason == "max_tokens":
        raise TriageError(f"{model} ran out of tokens ({max_tokens})")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise TriageError(f"{model} returned unparseable JSON: {exc}")


def screen(client, article: Dict) -> bool:
    body = (article.get("body_text") or article.get("summary") or "")[:SCREEN_CHARS]
    out = _ask(client, SCREEN_MODEL, SCREEN_SYSTEM, f"Title: {article['title']}\n\n{body}",
               SCREEN_SCHEMA, "low", 2000)
    return bool(out.get("is_candidate"))


def extract(client, article: Dict) -> List[Dict]:
    pub = article.get("published_utc")
    user = (f"Published: {pub.date() if hasattr(pub, 'date') else pub}\nTitle: {article['title']}\n\n"
            f"{article.get('body_text') or article.get('summary') or ''}")
    out = _ask(client, EXTRACT_MODEL, EXTRACT_SYSTEM, user, EXTRACT_SCHEMA, "medium", 6000)
    return list(out.get("events") or [])


# ── the run ───────────────────────────────────────────────────────────────────
def _article_dict(a) -> Dict:
    return {"url": a.url, "source": a.source, "title": a.title, "summary": a.summary,
            "body_text": a.body_text, "published_utc": a.published_utc}


def ingest_items(session, source_key: str, items: List[Dict], now: datetime.datetime,
                 since: Optional[datetime.datetime] = None) -> int:
    """Store feed items not seen before. Returns how many were new."""
    from db import NewsArticle
    known = {u for (u,) in session.query(NewsArticle.url).filter(
        NewsArticle.url.in_([i["url"] for i in items])).all()} if items else set()
    new = 0
    for it in items:
        if it["url"] in known:
            continue
        if since and it["published_utc"] and it["published_utc"] < since:
            continue
        score, passes = L.keyword_score(it["title"], it.get("summary") or "")
        session.add(NewsArticle(
            url=it["url"], source=source_key, published_utc=it["published_utc"], title=it["title"],
            summary=(it.get("summary") or "")[:2000], fetched_utc=now, keyword_score=score,
            # Fail OPEN on the cheap filter: a feed blurb is short, so an article that
            # fails here may still pass once its body is read. "skipped" is only set
            # after the body has been scored.
            triage_status="untriaged"))
        known.add(it["url"])
        new += 1
    session.flush()
    return new


def calls_today(session, now: datetime.datetime) -> int:
    """Model calls already made today. The free rule reader makes none and is not counted."""
    from db import NewsArticle
    start = datetime.datetime(now.year, now.month, now.day)
    return session.query(NewsArticle).filter(NewsArticle.triage_utc >= start,
                                             NewsArticle.triage_model.isnot(None),
                                             NewsArticle.triage_model != R.RULES_MODEL).count()


def _store_events(session, raw_events, art: Dict, now: datetime.datetime, stats: Dict,
                  verified_by: str, url: str) -> None:
    """Validate what a reader reported and store what survives. Shared by the Claude reader and
    the rule reader, so both are held to exactly the same checks."""
    for raw in raw_events:
        ev, why = L.validate_event(raw, art)
        if ev is None:
            stats["events_rejected"] += 1
            stats["reasons"].append(f"{url}: rejected - {'; '.join(why)}")
            continue
        red = L.redundancy(session, ev, art)
        row, how = L.merge_or_create(session, ev, art, now, verified_by=verified_by)
        if red and not row.redundant_with:
            row.redundant_with = red
        stats["events_created" if how == "created" else "events_merged"] += 1


def triage_pending(session, client, now: datetime.datetime,
                   fetch_body: Callable[[str], Optional[str]],
                   max_screens: int = None, max_extracts: int = None, max_bodies: int = None,
                   max_calls_per_day: int = None) -> Dict:
    """Score, screen and extract the articles still waiting. Budgeted; never silent.

    The limits default to the module's per-run caps; a backfill passes larger ones."""
    from db import NewsArticle
    max_screens = MAX_SCREENS if max_screens is None else max_screens
    max_extracts = MAX_EXTRACTS if max_extracts is None else max_extracts
    max_bodies = MAX_BODY_FETCHES if max_bodies is None else max_bodies
    max_calls_per_day = MAX_CALLS_PER_DAY if max_calls_per_day is None else max_calls_per_day
    stats = {"bodies": 0, "skipped": 0, "screened_out": 0, "extracted": 0, "events_created": 0,
             "events_merged": 0, "events_rejected": 0, "errors": 0, "untriaged_left": 0,
             "budget_hit": False, "no_key": client is None, "reasons": []}
    pend = session.query(NewsArticle).filter(NewsArticle.triage_status.in_(("untriaged", "error"))) \
        .order_by(NewsArticle.published_utc.desc().nullslast()).all()

    # 1. cheap filter on title + blurb; read the body only for plausible candidates
    work = []
    for a in pend:
        score, passes = L.keyword_score(a.title, a.summary or "")
        # An article a web search surfaced for a liquidity query has already been judged
        # relevant by the search; it goes straight to having its body read, where the
        # keyword filter and then the guards still apply.
        found_by_search = (a.source or "").startswith("search:")
        if not passes and not a.body_text and not found_by_search:
            # the blurb is too short to judge a long article; the title carrying an
            # actor and an action is the minimum worth a body fetch
            tscore, tpass = L.keyword_score(a.title, "")
            if tscore < 3:
                a.triage_status, a.keyword_score = "skipped", score
                stats["skipped"] += 1
                continue
        if not a.body_text:
            if stats["bodies"] >= max_bodies:
                stats["untriaged_left"] += 1         # out of fetch budget: try next run, do not judge it blind
                continue
            body = fetch_body(a.url)
            stats["bodies"] += 1
            if body:
                a.body_text = body
            else:
                stats["bodies_unreadable"] = stats.get("bodies_unreadable", 0) + 1
        score, passes = L.keyword_score(a.title, a.body_text or a.summary or "")
        a.keyword_score = score
        if not passes:
            a.triage_status = "skipped"
            stats["skipped"] += 1
            continue
        work.append(a)

    if client is None:
        # No key means no bill, not no reading: the free rule reader takes every candidate
        # through the same validation. Claude, when a key exists, reads what rules cannot.
        stats["mode"] = "rules"
        stats["reasons"].append("no ANTHROPIC_API_KEY: read by the rule reader (free, conservative)")
        for a in work:
            art = _article_dict(a)
            a.triage_model, a.triage_utc = R.RULES_MODEL, now
            raw_events = R.extract(art)
            a.triage_json = json.dumps(raw_events)[:20000]
            _store_events(session, raw_events, art, now, stats, "rules", a.url)
            a.triage_status = "extracted"
            stats["extracted"] += 1
        session.flush()
        return stats
    stats["mode"] = "claude"

    budget_left = max_calls_per_day - calls_today(session, now)
    screens = extracts = 0
    for a in work:
        art = _article_dict(a)
        try:
            if budget_left <= 0 or screens >= max_screens:
                raise _Budget()
            screens += 1
            budget_left -= 1
            a.triage_model, a.triage_utc = SCREEN_MODEL, now
            if not screen(client, art):
                a.triage_status = "screened_out"
                stats["screened_out"] += 1
                continue
            if budget_left <= 0 or extracts >= max_extracts:
                raise _Budget()
            extracts += 1
            budget_left -= 1
            a.triage_model = EXTRACT_MODEL
            raw_events = extract(client, art)
            a.triage_json = json.dumps(raw_events)[:20000]
            _store_events(session, raw_events, art, now, stats, "llm", a.url)
            a.triage_status = "extracted"
            stats["extracted"] += 1
        except _Budget:
            stats["budget_hit"] = True
            stats["untriaged_left"] += 1
        except TriageError as exc:
            a.triage_status, a.triage_json = "error", str(exc)[:500]
            stats["errors"] += 1
            stats["reasons"].append(f"{a.url}: {exc}")
        except Exception as exc:                         # network, SDK, anything else
            a.triage_status, a.triage_json = "error", f"{type(exc).__name__}: {exc}"[:500]
            stats["errors"] += 1
            stats["reasons"].append(f"{a.url}: {type(exc).__name__}: {exc}")
    session.flush()
    return stats


class _Budget(Exception):
    pass


def run(session, client=None, now: datetime.datetime = None, sources: Optional[List[Dict]] = None,
        fetch_listing: Optional[Callable] = None, fetch_body: Optional[Callable] = None,
        since: Optional[datetime.datetime] = None) -> Dict:
    """One full pass: every feed, then triage. Returns what happened, loudly."""
    from fetchers import news as N
    from fetchers import source_watch as SW
    now = now or datetime.datetime.utcnow()
    fetch_listing = fetch_listing or N.fetch_listing
    fetch_body = fetch_body or N.fetch_body
    out = {"sources": {}, "new_articles": 0, "problems": []}
    for src in (sources if sources is not None else N.NEWS_SOURCES):
        res = fetch_listing(src)
        if res.get("meta"):
            try:
                SW.record(session, res["meta"])
            except Exception as exc:                     # the watcher never breaks the fetch
                log.warning("fingerprint not recorded for %s: %s", src["key"], exc)
        if res.get("error") and not res["items"]:
            out["problems"].append(f"{src['key']}: {res['error']}")
        out["sources"][src["key"]] = len(res["items"])
        out["new_articles"] += ingest_items(session, src["key"], res["items"], now, since)
    out["triage"] = triage_pending(session, client, now, fetch_body)
    session.commit()
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Liquidity Watch: read Bangladesh's financial news")
    ap.add_argument("action", choices=("run", "list", "backfill"))
    ap.add_argument("--since", default="2026-07-01",
                    help="backfill: first date to cover (default: start of FY2026-27)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from db import get_session, init_db
    init_db()
    s = get_session()
    try:
        if a.action == "list":
            from db import LiquidityEvent
            for e in s.query(LiquidityEvent).order_by(LiquidityEvent.event_date.desc().nullslast()).limit(50):
                print(e.event_date, e.kind, e.amount_crore, e.status, e.counterparty, "|", e.summary)
            return 0
        if a.action == "backfill":
            client = get_client()
            if client is None:
                print("backfill needs ANTHROPIC_API_KEY (it searches the web through Claude)")
                return 1
            from engines.news_backfill import backfill
            res = backfill(s, client, datetime.date.fromisoformat(a.since))
            print(json.dumps(res, indent=2, default=str))
            t = res["triage"]
            return 1 if res["search_errors"] or t["errors"] or t["budget_hit"] or res["budget_hit"] else 0
        res = run(s, get_client())
        print(json.dumps(res, indent=2, default=str))
        t = res["triage"]
        # fail loud: the job must go red when the watch cannot do its job
        # No key is NOT a failed job (it would email every half hour until the key is
        # added); it is a standing defect the integrity check and the digest report.
        # One source blocking the runner (the Observer and Dhaka Tribune return 403 to a
        # datacenter address) is recorded by its fingerprint and printed above; it must
        # not turn the job red every half hour. Red means the watch cannot do its job:
        # the model calls failed, a budget was hit, or most of the sources are gone.
        sources_down = len(res["problems"]) * 2 > max(1, len(res["sources"]))
        return 1 if t["budget_hit"] or t["errors"] or sources_down else 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
