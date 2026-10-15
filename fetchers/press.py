"""BB's press-release listing, classified.

One page carries almost everything BB announces: OMO operations, treasury
auction results, auction notices, BGIIB (Islamic bond) results, and — since
08-Oct-2026 — bond buybacks. Three things this project needed were all sitting
in it:

  * the OMO fetch, whose links moved from `a.pdf-file` to `a.pdf-list` and
    went unnoticed for eleven days;
  * the first-ever buyback, 1,716.14 crore of cash that reached the market on
    12-Oct with nothing in the ladder to explain it;
  * BGIIB auctions, published weekly, of which this project captures none.

So the listing is read, classified and STORED, and what BB announces is then
checkable against what we hold. "BB published a buyback and we have no buyback
event" is a question only a stored listing can answer.

Classification is by keyword, in code, deliberately: it is a deterministic
transform of BB's own title text and there is no judgement in it. Nothing here
summarises or interprets — an "OTHER" release keeps its title verbatim and is
listed as such, because a category invented for a release nobody recognised is
worse than admitting the release is unrecognised.
"""
import datetime
import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger("press")

# Ordered: the first pattern that matches wins, so the specific beats the
# general ("Auction Notice of 10 years BGTB" is a NOTICE, not a BGTB result).
CATEGORIES = (
    ("BUYBACK",        r"buy\s*-?\s*back"),
    ("AUCTION_NOTICE", r"auction\s+notice|notice\s+of\s+auction"),
    ("OMO",            r"open\s+market\s+operation"),
    ("BGIIB",          r"bgiib|islami\s+investment\s+bond"),
    ("SUKUK",          r"sukuk|ijarah"),
    ("TBILL_RESULT",   r"treasury\s+bills?\s+auction"),
    ("TBOND_RESULT",   r"(treasury|govt\.?|government)\s+bond\s+auction|"
                       r"floating\s+rate\s+treasury\s+bond"),
    ("POLICY",         r"policy\s+rate|repo\s+rate|monetary\s+policy|bank\s+rate"),
    ("FX",             r"exchange\s+rate|foreign\s+exchange|remittance"),
)

_SERIAL = re.compile(r"/(pr\d+)_(\d{8})\.pdf", re.I)
_ISIN = re.compile(r"\b(BD[0-9A-Z]{10})\b")
# "as on 13 October 2026" / "held on 11 October 2026"
_WHEN = re.compile(r"(?:as\s+on|held\s+on|dated)\s+(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", re.I)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def classify(title: str) -> str:
    t = (title or "").lower()
    for name, pattern in CATEGORIES:
        if re.search(pattern, t):
            return name
    return "OTHER"


def subject_date(title: str) -> Optional[datetime.date]:
    """The date the release is ABOUT, which is not the date it was posted.

    "Open Market Operations as on 13 October 2026" was published on the 14th.
    Storing the publication date as the subject date is how a one-day lag
    becomes a permanent off-by-one in everything derived from it.
    """
    m = _WHEN.search(title or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    try:
        return datetime.date(int(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None


def classify_links(links: List[Dict]) -> dict:
    """Classify links already extracted from the listing.

    Split out so the DAILY OMO fetch can keep the whole listing from the page
    it downloads anyway. The listing is the watcher's only evidence, and a
    watcher that runs weekly would have taken a week to notice the first
    buyback — about as long as noticing it by hand.
    """
    rows: List[Dict] = []
    skipped: Dict[str, int] = {}

    for link in links:
        url = link.get("url") or ""
        title = (link.get("title") or "").strip()
        m = _SERIAL.search(url)
        if not m:
            skipped["link with no prNNNNN_YYYYMMDD filename"] = \
                skipped.get("link with no prNNNNN_YYYYMMDD filename", 0) + 1
            continue
        try:
            pub = datetime.datetime.strptime(m.group(2), "%Y%m%d").date()
        except ValueError:
            skipped["unparseable date in the filename"] = \
                skipped.get("unparseable date in the filename", 0) + 1
            continue
        isin = _ISIN.search(title)
        rows.append({
            "serial": m.group(1).lower(),
            "published_date": pub,
            "subject_date": subject_date(title),
            "title": title,
            "url": url,
            "category": classify(title),
            "isin": isin.group(1) if isin else None,
        })

    return {"rows": rows, "skipped": skipped}


def parse_listing(soup) -> dict:
    """Every release on the page, classified. Shares extract_pdf_links with the
    OMO fetcher, so a markup change is one fix, not two."""
    from fetchers.omo import extract_pdf_links
    from fetchers import source_watch as SW

    links, selector = extract_pdf_links(soup)
    out = classify_links(links)
    out["meta"] = SW.fingerprint(
        "press_listing",
        "https://www.bb.org.bd/en/index.php/mediaroom/press_release",
        selector=selector or "", rows_seen=len(links),
        rows_parsed=len(out["rows"]), skips=out["skipped"],
        error="" if out["rows"] else "no press releases parsed from the listing")
    return out


def fetch_press_releases() -> dict:
    """Read the listing. Needs the F5 handshake, like the OMO fetch."""
    from bs4 import BeautifulSoup
    from config import BB_PRESS_URL
    from fetchers.bb_session import bb_get, get_f5_cookies
    from fetchers import source_watch as SW
    try:
        ck, ua = get_f5_cookies(BB_PRESS_URL)
        html = bb_get(BB_PRESS_URL, ck, ua)
        if hasattr(html, "text"):
            html = html.text
    except Exception as exc:
        return {"rows": [], "skipped": {}, "reason": f"{type(exc).__name__} {exc}",
                "meta": SW.fingerprint("press_listing", BB_PRESS_URL,
                                       error=f"could not read the listing: {exc}")}
    out = parse_listing(BeautifulSoup(html, "lxml"))
    if not out["rows"]:
        out["reason"] = out["meta"].get("error") or "the listing parsed to nothing"
    return out
