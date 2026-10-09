"""The canary: read every BB page on purpose, even when nothing needs it.

Every structural check in this project runs as a side effect of a fetch. That
leaves a hole with a date on it: BB renamed its press-release links on a
weekend, and the first evidence anyone could have had was the next fetch of
that page. For a page we read daily that is a day; for the auction calendar,
the holiday list or a monthly results view it can be weeks, and the discovery
then happens at the worst possible moment — when the desk needs the number.

So this walks the sources deliberately, fingerprints the SHAPE of each one,
and stores nothing else. It reads; it never writes a figure. Run weekly in CI
and on demand:

    python canary.py              # fingerprint every source, report changes
    python canary.py --strict     # exit 1 when any source has moved

What it reports is what `fetchers/source_watch.py` decides: a reworded header
is a notice, a selector that stopped matching or a table that lost its columns
is a degradation. The verdicts land in `source_snapshots`, so the morning
digest carries them whether or not anyone runs this by hand.
"""
import argparse
import datetime
import logging
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from config import BB_AUC_CALENDAR_URL, BB_HOLIDAY_URL, BB_PRESS_URL, BB_TREASURY_URL
from db import get_session, init_db
from fetchers import source_watch as SW

log = logging.getLogger("canary")

# Pages whose first table IS the data. Each is read once, its header row
# fingerprinted, and nothing is parsed into the database.
TABLE_PAGES = (
    ("treasury", BB_TREASURY_URL),
    ("auction_calendar", BB_AUC_CALENDAR_URL),
    ("holidays", BB_HOLIDAY_URL),
    ("interbank_repo", "https://www.bb.org.bd/en/index.php/financialactivity/interbankrepo"),
    ("interbank_fx", "https://www.bb.org.bd/en/index.php/fxmarket/interbank_fxmarket"),
    ("call_money", "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market"),
    ("exchange_rates", "https://www.bb.org.bd/en/index.php/econdata/exchangerate"),
    ("ref_rates", "https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate"),
)


def _biggest_table(soup):
    """BB wraps its data table in layout tables, so "the first table" is not it.

    The data table is the one with the most rows. Picking by position is the
    same mistake as picking a column by position.
    """
    tables = soup.find_all("table")
    if not tables:
        return None
    return max(tables, key=lambda t: len(t.find_all("tr")))


def _headers_of(table) -> list:
    """The leading rows that are not data: BB marks them <th>, mostly."""
    header_rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        ths = sum(1 for c in cells if c.name == "th")
        if ths >= max(1, len(cells) // 2):
            header_rows.append(tr)
        elif header_rows:
            break
        elif len(header_rows) == 0 and not any(
                c.get_text(strip=True).replace(".", "").replace(",", "").isdigit()
                for c in cells):
            header_rows.append(tr)   # a header row BB forgot to mark up
        else:
            break
    return SW.header_columns(header_rows)


def look_at_table_page(name: str, url: str) -> dict:
    from bs4 import BeautifulSoup
    from fetchers.bb_session import bb_get, get_f5_cookies
    try:
        ck, ua = get_f5_cookies(url)
        html = bb_get(url, ck, ua)
        if hasattr(html, "text"):
            html = html.text
    except Exception as exc:
        return SW.fingerprint(name, url, error=f"could not read the page: "
                                               f"{type(exc).__name__} {exc}")
    soup = BeautifulSoup(html, "lxml")
    table = _biggest_table(soup)
    if table is None:
        return SW.fingerprint(name, url, error="the page has no table at all")
    rows = table.find_all("tr")
    headers = _headers_of(table)
    # "Parsed" here means "rows that look like data": the canary does not run
    # the real parsers, so this is the honest count it can make.
    data_rows = [tr for tr in rows if tr.find_all("td") and tr not in rows[:len(headers) and 2]]
    return SW.fingerprint(name, url, selector="largest table", headers=headers,
                          rows_seen=len(rows), rows_parsed=len(data_rows))


def look_at_press_page() -> dict:
    """The page whose markup change cost eleven days of OMO data."""
    from fetchers.omo import fetch_omo_links
    try:
        info = fetch_omo_links(days_back=21, max_files=10)
    except Exception as exc:
        return SW.fingerprint("omo_press", BB_PRESS_URL,
                              error=f"could not read the listing: {type(exc).__name__} {exc}")
    return SW.fingerprint(
        "omo_press", BB_PRESS_URL, selector=info.get("selector") or "",
        rows_seen=info.get("rows_seen") or 0, rows_parsed=len(info.get("links") or []),
        skips={"not an OMO release": info.get("skipped_not_omo") or 0,
               "older than the window": info.get("skipped_too_old") or 0},
        error=("" if info.get("links") else
               f"{info.get('anchors_found')} anchors found, none an OMO release"))


def run(strict: bool = False) -> dict:
    init_db()
    s = get_session()
    moved, checked = [], []
    try:
        for snap in [look_at_press_page()] + [look_at_table_page(n, u)
                                              for n, u in TABLE_PAGES]:
            verdict, reasons = SW.record(s, snap)
            s.commit()
            checked.append(snap["source"])
            if verdict != "ok":
                moved.append({"source": snap["source"], "verdict": verdict,
                              "reasons": reasons, "url": snap.get("url")})
            print(f"{snap['source']:<18} {verdict:<9} "
                  f"{snap.get('column_count')} cols, "
                  f"{snap.get('rows_parsed')}/{snap.get('rows_seen')} rows"
                  + (f" — {'; '.join(reasons)}" if reasons else ""))
    finally:
        s.close()
    print(f"\n{len(checked)} source(s) read, {len(moved)} moved.")
    if moved and strict:
        sys.exit(1)
    return {"checked": checked, "moved": moved,
            "as_of": datetime.datetime.utcnow().isoformat(timespec="seconds")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fingerprint every BB source on purpose")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any source has moved (for CI)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    run(strict=a.strict)
