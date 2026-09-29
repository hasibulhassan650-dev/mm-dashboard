"""
backfill_omo.py — pull every available BB OMO press release into omo_transactions.

WHAT THIS FOUND, AND WHY IT MATTERS
Call money and the policy corridor both looked like hard data gaps and turned out
to be short query windows — the source had years of history, the caller only ever
asked for weeks. OMO was checked the same way and the answer is different.

BB's press-release archive IS deep: 90 releases in 2023, 310 in 2024, 559 in
2025, 377 in 2026. But OMO-titled releases exist ONLY from 2026-02-17:

    year   all press releases   OMO-titled
    2023          90                 0
    2024         310                 0
    2025         559                 0
    2026         377               134

So BB did not publish open-market operations as press releases before Feb 2026.
This is a genuine limit of the source, not of the query, and no amount of
paging or filtering will produce a longer OMO series. Recorded here so nobody
(including me) burns another hour rediscovering it.

WHAT THIS STILL BUYS
The default listing that fetchers/omo.py pages through only surfaces ~61 OMO
links. Posting the archive form surfaces all 134, which reaches back to
2026-02-17 instead of 2026-03-05 and fills gaps inside the range. Worth having,
and this route needs no Chrome — the form answers a plain impersonated POST.

Usage:  python backfill_omo.py
"""
import datetime
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from db import init_db, get_session, OMOTransaction        # noqa: E402
from fetchers.omo import parse_omo_pdf                     # noqa: E402
from engines.pipeline import _store_omo_txns               # noqa: E402
from config import BB_PRESS_URL                            # noqa: E402

log = logging.getLogger("backfill_omo")

# The form's date fields are inert — every range returns the same full archive —
# so a wide range is sent purely to trigger the search rather than to filter.
FORM = {"from_date": "01/01/2015", "to_date": "31/12/2026",
        "keyword": "", "title": "", "pressrelease_submit": "Search"}
OMO_TITLE = "open market operations"


def omo_links() -> list[tuple[str, datetime.date | None]]:
    """(pdf_url, date-from-filename) for every OMO press release BB lists."""
    from curl_cffi import requests as cr
    from bs4 import BeautifulSoup

    s = cr.Session(impersonate="chrome")
    s.get(BB_PRESS_URL, timeout=60)
    r = s.post(BB_PRESS_URL, data=FORM, timeout=120)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    out, seen = [], set()
    for a in soup.find_all("a", class_="pdf-file"):
        url = (a.get("pdf-link") or "").strip()
        if not url or url in seen:
            continue
        td = a.find_parent("td")
        title = td.get_text(" ", strip=True).lower() if td else ""
        if OMO_TITLE not in title:
            continue
        seen.add(url)
        m = re.search(r"_(\d{8})\.pdf", url)
        d = None
        if m:
            try:
                d = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
            except ValueError:
                pass
        out.append((url, d))
    out.sort(key=lambda x: x[1] or datetime.date.min)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    init_db()
    session = get_session()
    try:
        before = session.query(OMOTransaction).count()
        links = omo_links()
        if not links:
            log.warning("no OMO links found — BB may have blocked the request")
            return 1
        dated = [d for _u, d in links if d]
        log.info("%d OMO releases listed, %s -> %s; %d rows already stored",
                 len(links), min(dated) if dated else "?", max(dated) if dated else "?", before)

        from curl_cffi import requests as cr
        s = cr.Session(impersonate="chrome")
        rows, failed = [], 0
        for i, (url, d) in enumerate(links, 1):
            try:
                pdf = s.get(url, timeout=90).content
                rows.extend(parse_omo_pdf(pdf, d, url))
            except Exception as exc:
                failed += 1
                log.warning("  %s failed: %s", url.rsplit("/", 1)[-1], exc)
            if i % 25 == 0:
                log.info("  parsed %d/%d releases, %d rows so far", i, len(links), len(rows))

        log.info("parsed %d rows from %d releases (%d failed)", len(rows), len(links), failed)
        if not rows:
            return 1

        saved, superseded = _store_omo_txns(session, rows, datetime.datetime.utcnow())
        session.commit()

        after = session.query(OMOTransaction).count()
        first = session.query(OMOTransaction).order_by(OMOTransaction.transaction_date).first()
        last = session.query(OMOTransaction).order_by(OMOTransaction.transaction_date.desc()).first()
        log.info("done: saved=%d superseded=%d; table %d -> %d rows, %s -> %s",
                 saved, superseded, before, after,
                 first.transaction_date if first else None,
                 last.transaction_date if last else None)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
