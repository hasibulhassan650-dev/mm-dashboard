"""Bangladesh's financial news, read for liquidity events BB's own data does not hold.

BB's press releases and tables cover what BB does. They do not cover what the
government does with cash that reaches the banks - an LNG subsidy released to
Petrobangla, arrears settled, support to a weak bank - and those move liquidity
just as an OMO does. They surface first, and often only, in the press.

This module only FETCHES: a feed listing, then the article body for the few that
pass the keyword filter. Deciding what an article means is engines/liquidity_news.py.

Sources are a registry, each verified to answer on 25-Oct-2026. A source that
stops parsing is detected by source_watch (a fingerprint per source, key
`news:<key>`), the same way a BB page is; it is never silently dropped. RSS only
reaches back the last 10-100 items, so history comes from the backfill path.
"""
import datetime
import email.utils
import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("news")

BODY_CAP = 12000
_POLITE_SECONDS = 1.0           # one request a second per host, at most

# Verified feeds. `label` is what the desk sees. Add a source only after a real
# fetch returned items; one that does not parse is left out, not kept broken.
NEWS_SOURCES: List[Dict] = [
    {"key": "dailystar_business", "label": "The Daily Star - Business",
     "url": "https://www.thedailystar.net/business/rss.xml"},
    {"key": "dailystar_economy", "label": "The Daily Star - Economy",
     "url": "https://www.thedailystar.net/business/economy/rss.xml"},
    {"key": "dailystar_banking", "label": "The Daily Star - Banking",
     "url": "https://www.thedailystar.net/business/banking/rss.xml"},
    {"key": "tbs_economy", "label": "The Business Standard - Economy",
     "url": "https://www.tbsnews.net/economy/rss.xml"},
    {"key": "tbs_banking", "label": "The Business Standard - Banking",
     "url": "https://www.tbsnews.net/economy/banking/rss.xml"},
    {"key": "prothomalo_en", "label": "Prothom Alo English",
     "url": "https://en.prothomalo.com/feed"},
    {"key": "dhakatribune_business", "label": "Dhaka Tribune - Business",
     "url": "https://www.dhakatribune.com/feed/business"},
    {"key": "observerbd", "label": "The Daily Observer",
     "url": "https://www.observerbd.com/rss.php"},
]


def canonical_url(u: str) -> str:
    """The dedupe key: no query string, no fragment, no trailing slash."""
    p = urlsplit((u or "").strip())
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", ""))


def _text(html: str) -> str:
    from bs4 import BeautifulSoup
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)).strip()


def _when(s: Optional[str]) -> Optional[datetime.datetime]:
    """RFC-822 or ISO date -> naive UTC. None if unreadable (never guessed)."""
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is not None:
        d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return d


def parse_rss(xml: str) -> List[Dict]:
    """RSS 2.0 / Atom items -> [{url, title, summary, published_utc}]."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(xml or "", "xml")
    out = []
    for it in soup.find_all(["item", "entry"]):
        link = it.find("link")
        url = (link.get("href") if link is not None and link.get("href") else
               (link.get_text(strip=True) if link is not None else ""))
        title = it.find("title")
        if not url or title is None:
            continue
        desc = it.find(["description", "summary", "content:encoded", "content"])
        when = it.find(["pubDate", "published", "updated", "dc:date"])
        out.append({
            "url": canonical_url(url),
            "title": _text(title.get_text()),
            "summary": _text(desc.get_text()) if desc is not None else "",
            "published_utc": _when(when.get_text()) if when is not None else None,
        })
    return out


def fetch_listing(src: Dict) -> Dict:
    """One feed -> items + a structural fingerprint, or a reason it failed."""
    from curl_cffi import requests as cr

    from fetchers import source_watch as SW
    key = f"news:{src['key']}"
    try:
        r = cr.get(src["url"], impersonate="chrome", timeout=30)
    except Exception as exc:
        return {"items": [], "error": f"{type(exc).__name__}: {exc}",
                "meta": SW.fingerprint(key, src["url"], transport_error=f"{type(exc).__name__}: {exc}")}
    if r.status_code != 200:
        return {"items": [], "error": f"HTTP {r.status_code}",
                "meta": SW.fingerprint(key, src["url"], transport_error=f"HTTP {r.status_code}")}
    items = parse_rss(r.text)
    seen = (r.text or "").count("<item") + (r.text or "").count("<entry")
    meta = SW.fingerprint(key, src["url"], selector="rss item",
                          headers=["title", "link", "pubdate"], rows_seen=seen,
                          rows_parsed=len(items),
                          skips={"item without link/title": seen - len(items)} if seen > len(items) else {},
                          error="" if items else "the feed carries no readable items")
    return {"items": items, "error": None if items else "no items", "meta": meta}


# Most specific first. The last two are generic: <article> on The Daily Star wraps
# the sidebars and "Editor's Pick" too, so a class containing "body" - taking the
# SMALLEST such node that still holds a real article - is tried before it.
_BODY_SELECTORS = ("div[itemprop=articleBody]", "div.article-details", "div.article-content",
                   "div.story-element-text", "div.field-name-body", "div.detailsPage",
                   "div.news-details", "div.content-details", "div[class*=body]", "article")


def extract_body(html: str) -> str:
    """Article text from a page. Container first, joined paragraphs as fallback."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    for t in soup(["script", "style", "nav", "footer", "aside", "form"]):
        t.decompose()
    for sel in _BODY_SELECTORS:
        texts = [re.sub(r"\s+", " ", n.get_text(" ", strip=True)) for n in soup.select(sel)]
        texts = [x for x in texts if len(x) > 400]
        if texts:
            return min(texts, key=len)[:BODY_CAP]
    paras = [re.sub(r"\s+", " ", p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    txt = " ".join(p for p in paras if len(p) > 40)
    return txt[:BODY_CAP]


def fetch_body(url: str, sleep: float = _POLITE_SECONDS) -> Optional[str]:
    """The article's text, or None when it could not be read (never a guess)."""
    from curl_cffi import requests as cr
    if sleep:
        time.sleep(sleep)
    try:
        r = cr.get(url, impersonate="chrome", timeout=30)
    except Exception as exc:
        log.warning("article not read %s: %s", url, type(exc).__name__)
        return None
    if r.status_code != 200:
        log.warning("article not read %s: HTTP %s", url, r.status_code)
        return None
    body = extract_body(r.text)
    return body or None
