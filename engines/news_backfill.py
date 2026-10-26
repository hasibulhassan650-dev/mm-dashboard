"""Catch the Liquidity Watch up on history, automatically, without a person searching.

The RSS feeds only reach back a few days. To find what happened since 1-July (the start
of the fiscal year) the watch asks Claude to SEARCH the web for each kind of event, month
by month, restricted to Bangladesh's own news outlets, and then reads what comes back
through exactly the same pipeline as a live article:

    discovery (Claude + the web_search server tool)  ->  candidate URLs only
    reading   (news_triage)                          ->  fetch the page, keyword filter,
                                                         screen, extract
    belief    (liquidity_news)                       ->  the quote must be in the page,
                                                         the amount is re-read by code

Search is for FINDING articles. The model's own summary of what it found is thrown away;
only the URLs are kept, because an event is believed only on the strength of a sentence
that is verifiably in the article. A search that returns a rumour produces an article that
extracts nothing.

Events found this way are history, not news: they are marked as already announced so the
first backfill does not email a hundred old events.

Everything runs in CI with the repository's own ANTHROPIC_API_KEY, so it costs the desk a
few dollars once and costs nobody a conversation.
"""
import datetime
import logging
from typing import Callable, Dict, List, Optional

from engines import liquidity_news as L
from engines import news_triage as T

log = logging.getLogger("news_backfill")

SEARCH_MODEL = "claude-sonnet-5-5"
MAX_SEARCHES_PER_RUN = 60
# Bangladeshi outlets only: a search that wanders to a foreign aggregator returns pages we
# cannot read and sources the desk does not trust.
ALLOWED_DOMAINS = [
    "thedailystar.net", "tbsnews.net", "thefinancialexpress.com.bd", "en.prothomalo.com",
    "dhakatribune.com", "newagebd.net", "unb.com.bd", "bssnews.net", "bdnews24.com",
    "observerbd.com", "en.bonikbarta.com", "jagonews24.com", "daily-sun.com",
]

# One discovery query per kind of event worth catching. {month} is "July 2026" etc.
QUERIES = [
    "Finance Division released Tk crore LNG subsidy Petrobangla {month}",
    "Finance Division released subsidy BPDB power sector Tk crore {month}",
    "fertiliser subsidy arrears paid Tk crore Bangladesh {month}",
    "Bangladesh Bank liquidity support to bank Tk crore {month}",
    "Bangladesh Bank money printing government borrowing ways and means advance {month}",
    "Bangladesh Bank CRR SLR change banks {month}",
    "Bangladesh Bank refinance pre-finance scheme fund Tk crore {month}",
    "special bond issued power fertiliser arrears banks Tk crore {month}",
    "national savings certificate net sales repayment Tk crore {month}",
    "Bangladesh Bank bills mop up liquidity {month}",
    "IMF World Bank ADB budget support tranche released Bangladesh {month}",
    "deposit insurance payout bank resolution Bangladesh Tk crore {month}",
]


def months_between(since: datetime.date, today: datetime.date) -> List[str]:
    """['July 2026', 'August 2026', ...] from `since`'s month to `today`'s."""
    out, y, m = [], since.year, since.month
    while (y, m) <= (today.year, today.month):
        out.append(datetime.date(y, m, 1).strftime("%B %Y"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _page_age(v) -> Optional[datetime.datetime]:
    """The search tool's `page_age` is free text ("October 20, 2026"); an unreadable one is None."""
    if not v:
        return None
    try:
        from dateutil import parser
        return parser.parse(str(v), default=datetime.datetime(2000, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return None


def results_of(resp) -> List[Dict]:
    """Every search result URL in a response. An error block is skipped, not raised: a server
    tool reports failure in a 200 whose result `content` is an object, not a list."""
    out = []
    for b in getattr(resp, "content", []) or []:
        if getattr(b, "type", "") != "web_search_tool_result":
            continue
        content = getattr(b, "content", None)
        if not isinstance(content, list):
            log.warning("search error: %s", getattr(content, "error_code", content))
            continue
        for r in content:
            if getattr(r, "type", "") == "web_search_result" and getattr(r, "url", None):
                out.append({"url": r.url, "title": getattr(r, "title", "") or "",
                            "published_utc": _page_age(getattr(r, "page_age", None))})
    return out


def discover(client, query: str) -> List[Dict]:
    """One search. The model's prose is ignored; only the result URLs are used."""
    resp = client.messages.create(
        model=SEARCH_MODEL, max_tokens=4000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4,
                "allowed_domains": ALLOWED_DOMAINS}],
        output_config={"effort": "low"},
        messages=[{"role": "user", "content":
                   f"Search the web for Bangladeshi news about: {query}. Run the search and stop; "
                   f"do not summarise."}])
    if resp.stop_reason == "refusal":
        raise T.TriageError("the search model declined the request")
    return results_of(resp)


def backfill(session, client, since: datetime.date, now: datetime.datetime = None,
             queries: List[str] = None, max_searches: int = MAX_SEARCHES_PER_RUN,
             fetch_body: Optional[Callable] = None, discover_fn: Callable = None) -> Dict:
    """Search month by month, ingest what is found, read it, and mark the result as history."""
    from fetchers import news as N
    now = now or datetime.datetime.utcnow()
    discover_fn = discover_fn or discover
    fetch_body = fetch_body or N.fetch_body
    queries = queries or QUERIES
    stats = {"searches": 0, "urls_found": 0, "new_articles": 0, "search_errors": [], "budget_hit": False}
    # Articles the free rule reader has already read get a second, better reading by Claude:
    # the rules are the floor, and a key arriving later should lift what is already stored.
    from sqlalchemy import text as _t
    stats["reread_by_claude"] = session.execute(_t(
        "UPDATE news_articles SET triage_status = 'untriaged' "
        "WHERE triage_model = :m AND body_text IS NOT NULL"), {"m": T.R.RULES_MODEL}).rowcount
    since_dt = datetime.datetime.combine(since, datetime.time())
    for month in months_between(since, now.date()):
        for q in queries:
            if stats["searches"] >= max_searches:
                stats["budget_hit"] = True
                break
            stats["searches"] += 1
            try:
                found = discover_fn(client, q.format(month=month))
            except Exception as exc:
                stats["search_errors"].append(f"{month} / {q[:40]}: {type(exc).__name__}: {exc}")
                continue
            stats["urls_found"] += len(found)
            items = [{"url": N.canonical_url(f["url"]), "title": f["title"], "summary": "",
                      "published_utc": f["published_utc"]} for f in found]
            # source "search:<domain-ish>" tells triage to read the body without the
            # title-only gate: the search already judged relevance, the guards judge truth.
            stats["new_articles"] += T.ingest_items(session, "search:web", items, now, since_dt)
    session.flush()
    # Read them all, with the backfill's larger budgets.
    stats["triage"] = T.triage_pending(session, client, now, fetch_body,
                                       max_screens=250, max_extracts=100, max_bodies=300,
                                       max_calls_per_day=350)
    stats["marked_as_history"] = mark_history(session, now)
    session.commit()
    return stats


def mark_history(session, now: datetime.datetime) -> int:
    """Events found by a backfill are history: never announce them."""
    from sqlalchemy import text
    return session.execute(text(
        "UPDATE liquidity_events SET notified_utc = :n WHERE notified_utc IS NULL"), {"n": now}).rowcount
