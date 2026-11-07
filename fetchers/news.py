"""Bangladesh's financial news, read for liquidity events BB's own data does not hold.

BB's press releases and tables cover what BB does. They do not cover what the
government does with cash that reaches the banks - an LNG subsidy released to
Petrobangla, arrears settled, support to a weak bank - and those move liquidity
just as an OMO does. They surface first, and often only, in the press.

This module only FETCHES: a listing, then the article body for the few that
pass the keyword filter. Deciding what an article means is engines/liquidity_news.py.

Nobody searches the web by hand for this: the engine fetches on its own schedule (the PC task every 5 minutes, the
GitHub workflow as a backup) and watches itself. Every source is a registry entry; adding one is a dict, not code.

  * `type: rss`      an RSS/Atom feed (the newest 5-100 items; it scrolls off between runs on a slow schedule);
  * `type: sitemap`  a Google News sitemap: the last ~2 days of stories with titles and dates, which is what stops
                     a quiet schedule from missing stories that scrolled off a short feed. `section` keeps only the
                     business pages; a sitemap without titles gets one from the URL slug until the body is read.

A source that stops parsing is detected twice: by source_watch (a layout fingerprint per source, key `news:<key>`) and
by `news_source_runs`, where every run records every source's outcome - including one that could not be reached at all,
which the fingerprint cannot store. A source is never silently dropped. Sources with `enabled: False` are registered
but not read (Bangla ones, until the Bangla amount and request guards exist); the engine skips them and so do the alarms.
"""
import datetime
import email.utils
import logging
import os
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("news")

BODY_CAP = 12000
_POLITE_SECONDS = 1.0           # one request a second per host, at most

# `label` is what the desk sees. A source that answers nothing is reported by the alarms, never kept quiet; one that is
# registered but not yet safe to read says so with `enabled: False` and a `why`.
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
    {"key": "dhakatribune_business", "label": "Dhaka Tribune - Business",
     "url": "https://www.dhakatribune.com/feed/business"},
    # News sitemaps: about two days of stories each, with titles and dates. The three RSS feeds above them stay until
    # a week of news_source_runs shows both machines reading the sitemaps; URL dedupe makes the overlap free.
    {"key": "dailystar_gn", "label": "The Daily Star - Business (news sitemap)", "type": "sitemap",
     "url": "https://www.thedailystar.net/googlenews.xml", "section": r"/business/"},
    {"key": "tbs_gn", "label": "The Business Standard - Economy (news sitemap)", "type": "sitemap",
     "url": "https://www.tbsnews.net/googlenews.xml", "section": r"/economy/"},
    {"key": "bdnews24_business", "label": "bdnews24 - Business", "type": "sitemap",
     "url": "https://bdnews24.com/news_sitemap.xml", "section": r"/business/"},
    # replaces the site-wide /feed (5 items spanning under 2 hours, every summary empty)
    {"key": "prothomalo_en_business", "label": "Prothom Alo English - Business", "type": "sitemap",
     "url": "https://en.prothomalo.com/news_sitemap.xml", "section": r"/business/"},
    {"key": "newage_business", "label": "New Age - Business", "type": "sitemap",
     "url": "https://www.newagebd.net/news-sitemap/1.xml", "limit": 400,
     "section": r"/post/(economy|trade-commerce|business-miscellany|banking)/"},
    # Bangla: registered, not read. The reader cannot yet check a Bangla amount (digits and the units কোটি, লাখ,
    # হাজার কোটি) or tell a Bangla request from a payment, and an unchecked amount is how a wrong figure gets stored.
    {"key": "prothomalo_bn_business", "label": "Prothom Alo (Bangla) - Business", "type": "sitemap", "lang": "bn",
     "url": "https://www.prothomalo.com/news_sitemap.xml", "section": r"/business/",
     "enabled": False, "why": "Bangla amount and request guards not built yet"},
    {"key": "samakal_economics", "label": "Samakal - Economics", "type": "sitemap", "lang": "bn",
     "url": "https://samakal.com/news_sitemap.xml", "section": r"/economics/", "naive_utc_offset": 6,
     "enabled": False, "why": "Bangla amount and request guards not built yet"},
    {"key": "tbs_bn_economy", "label": "The Business Standard (Bangla) - Economy", "lang": "bn",
     "url": "https://www.tbsnews.net/bangla/economy/rss.xml",
     "enabled": False, "why": "Bangla amount and request guards not built yet"},
]


def active_sources() -> List[Dict]:
    """The sources the engine reads and the alarms watch: registered and not switched off."""
    return [s for s in NEWS_SOURCES if s.get("enabled", True)]


def machine() -> str:
    """Which computer ran this: 'pc' or 'github'. A source one machine cannot reach (GitHub's address is blocked by
    some sites) is judged by whether ANY machine read it."""
    return os.environ.get("NEWS_MACHINE") or ("github" if os.environ.get("GITHUB_ACTIONS") else "pc")


def canonical_url(u: str) -> str:
    """The dedupe key: no query string, no fragment, no trailing slash."""
    p = urlsplit((u or "").strip())
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", ""))


def _text(html: str) -> str:
    from bs4 import BeautifulSoup
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "lxml").get_text(" ", strip=True)).strip()


def _when(s: Optional[str], naive_utc_offset: int = 0) -> Optional[datetime.datetime]:
    """RFC-822 or ISO date -> naive UTC. None if unreadable (never guessed).

    A date with no time zone is taken as UTC unless the source is known to print local time: `naive_utc_offset` is its
    offset in hours (Bangladesh is +6), subtracted so a Dhaka 19:14 is not stored as 19:14 UTC."""
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
    elif naive_utc_offset:
        d = d - datetime.timedelta(hours=naive_utc_offset)
    return d


def slug_title(url: str) -> str:
    """A readable title from a URL slug, for a sitemap that carries none: '.../bb-releases-tk-3000cr-lng-subsidy-4821'
    -> 'bb releases tk 3000cr lng subsidy'. Only a stand-in for the keyword filter: once the body is read the page's
    own text decides, and the title is never shown as if it were the headline."""
    seg = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    seg = re.sub(r"\.(html?|php|aspx?)$", "", seg, flags=re.I)
    words = [w for w in re.split(r"[-_]+", seg) if w]
    while words and re.fullmatch(r"\d{4,}", words[-1]):
        words.pop()
    while words and re.fullmatch(r"\d{4,}", words[0]):
        words.pop(0)
    return " ".join(words)


def parse_news_sitemap(xml: str, section: Optional[str] = None, limit: int = 400,
                       naive_utc_offset: int = 0) -> List[Dict]:
    """A Google News sitemap (or a plain one) -> [{url, title, summary, published_utc}], newest first as listed.

    `section` is a regex on the URL: only those pages are kept (a sitemap lists the whole newsroom). The title is the
    sitemap's own when it has one, else the URL slug."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(xml or "", "xml")
    rx = re.compile(section) if section else None
    out: List[Dict] = []
    for u in soup.find_all("url"):
        loc = u.find("loc")
        if loc is None or not loc.get_text(strip=True):
            continue
        url = canonical_url(loc.get_text(strip=True))
        if rx and not rx.search(url):
            continue
        t = u.find("news:title")
        when = u.find("news:publication_date") or u.find("lastmod")
        title = _text(t.get_text()) if t is not None else slug_title(url)
        if not title:
            continue
        out.append({"url": url, "title": title, "summary": "",
                    "published_utc": _when(when.get_text(), naive_utc_offset) if when is not None else None})
        if len(out) >= limit:
            break
    return out


def parse_rss(xml: str, naive_utc_offset: int = 0) -> List[Dict]:
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
            "published_utc": _when(when.get_text(), naive_utc_offset) if when is not None else None,
        })
    return out


def fetch_listing(src: Dict) -> Dict:
    """One source -> items + a structural fingerprint, or a reason it failed."""
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
    offset = src.get("naive_utc_offset", 0)
    if src.get("type") == "sitemap":
        items = parse_news_sitemap(r.text, src.get("section"), src.get("limit", 400), offset)
        # rows_seen counts only the pages of OUR section: a sitemap lists the whole newsroom, so the skipped rest is
        # not a parser failure, and a layout change that empties the section still shows as "no items"
        meta = SW.fingerprint(key, src["url"], selector="news sitemap url",
                              headers=["loc", "news:title", "news:publication_date"], rows_seen=len(items),
                              rows_parsed=len(items),
                              error="" if items else "the sitemap holds no page in the section "
                                                      f"{src.get('section') or '(any)'}")
        return {"items": items, "error": None if items else "no items", "meta": meta}
    items = parse_rss(r.text, offset)
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
