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
#
# `plain` means the production fetcher reads this page with plain curl_cffi and
# no F5 handshake — interbank_repo, the FX pages and the calendar all do. The
# canary must read each page the way production reads it, or it reports on
# something the pipeline never sees. The calendar taught that lesson: BB's TSPD
# cookies are PATH-BOUND (fetchers/auction_live.py says so in as many words),
# so cookies captured on the treasury page do not clear the calendar URL, and
# the canary's first run called the calendar "degraded" when the only thing
# wrong was the canary.
TABLE_PAGES = (
    ("treasury", BB_TREASURY_URL, False),
    ("auction_calendar", BB_AUC_CALENDAR_URL, True),
    ("holidays", BB_HOLIDAY_URL, True),
    ("interbank_repo", "https://www.bb.org.bd/en/index.php/financialactivity/interbankrepo", True),
    ("interbank_fx", "https://www.bb.org.bd/en/index.php/fxmarket/interbank_fxmarket", True),
    ("call_money", "https://www.bb.org.bd/en/index.php/monetaryactivity/call_money_market", False),
    ("exchange_rates", "https://www.bb.org.bd/en/index.php/econdata/exchangerate", True),
    ("ref_rates", "https://www.bb.org.bd/en/index.php/monetaryactivity/money_market_ref_rate", False),
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


def _read_plain(url: str) -> str:
    """curl_cffi with Chrome TLS impersonation — the way most fetchers read BB."""
    from curl_cffi import requests as cr
    r = cr.get(url, impersonate="chrome", timeout=30)
    return r.text or ""


def _soup(html: str):
    """Parse the way the real fetchers parse.

    BB's 2026 redesign renders the auction calendar as div grids
    (.table_caption / .row-header / .row-data) with no <table> element at all,
    and fetchers/auction_live.py rebuilds table markup before parsing. The
    canary's first run reported the calendar "degraded — no table at all",
    which was true of the HTML and false about the data: a canary that reads a
    page differently from production reports on something the pipeline never
    sees, and a false alarm every week is worse than no canary.
    """
    from bs4 import BeautifulSoup
    from fetchers.auction_live import _normalize_div_layout
    try:
        html = _normalize_div_layout(html)
    except Exception:
        pass
    return BeautifulSoup(html, "lxml")


# Canary fingerprints are namespaced. The canary reads each page's DEFAULT
# view; the production fetchers POST month forms and read different tables, so
# sharing a source name would give one source two rival shapes that each report
# the other as a change, for ever.
CANARY = "canary:"


def look_at_table_page(name: str, url: str, plain: bool = True, session_ck=None) -> dict:
    from bs4 import BeautifulSoup
    from fetchers.bb_session import bb_get, get_f5_cookies
    html, how = "", ""
    try:
        if plain:
            html, how = _read_plain(url), "curl_cffi"
        got_table = bool(html) and _biggest_table(_soup(html)) is not None
        if not got_table:
            # Either this page needs the F5 handshake, or plain reading came
            # back without a table. One handshake is shared across the pages
            # that need it: get_f5_cookies launches Chrome, and BB has
            # throttled this project for bursts before.
            ck, ua = session_ck if session_ck else get_f5_cookies(url)
            html = bb_get(url, ck, ua)
            if hasattr(html, "text"):
                html = html.text
            how = "f5+curl_cffi"
    except Exception as exc:
        # Transport, not structure: a failed handshake says nothing about
        # BB's page, and recording it as a shape would overwrite the real one.
        return SW.fingerprint(CANARY + name, url,
                              transport_error=f"{type(exc).__name__} {exc}")
    soup = _soup(html)
    table = _biggest_table(soup)
    if table is None:
        return SW.fingerprint(CANARY + name, url, selector="largest table",
                              error=f"read {len(html):,} bytes with {how} and found no "
                                f"table at all — BB is serving something else here")
    rows = table.find_all("tr")
    headers = _headers_of(table)
    # "Parsed" here means "rows that look like data": the canary never runs the
    # real parsers, so a row with data cells and no header cells is the honest
    # count it can make.
    data_rows = [tr for tr in rows if tr.find_all("td") and not tr.find_all("th")]
    # The selector names the PAGE's shape, never how we reached it. Putting the
    # transport in here ("largest table via curl_cffi") made the fallback from
    # plain reading to the F5 handshake look like BB restructuring the page —
    # a built-in false alarm, in the one component whose whole job is to cry
    # only when something real moves. `how` is for the log line.
    return SW.fingerprint(CANARY + name, url, selector="largest table", headers=headers,
                          rows_seen=len(rows), rows_parsed=len(data_rows))


def look_at_press_page() -> dict:
    """The page whose markup change cost eleven days of OMO data."""
    from fetchers.omo import fetch_omo_links
    try:
        info = fetch_omo_links(days_back=21, max_files=10)
    except Exception as exc:
        return SW.fingerprint(CANARY + "omo_press", BB_PRESS_URL,
                              transport_error=f"{type(exc).__name__} {exc}")
    return SW.fingerprint(
        CANARY + "omo_press", BB_PRESS_URL, selector=info.get("selector") or "",
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
        from fetchers.bb_session import get_f5_cookies
        try:
            # Only the pages marked as needing it; the rest read plainly.
            session_ck = (get_f5_cookies(BB_TREASURY_URL)
                          if any(not pl for _, _, pl in TABLE_PAGES) else None)
        except Exception as exc:
            log.warning("could not pass the F5 challenge once for all pages (%s) — "
                        "each page will try on its own", exc)
            session_ck = None
        for snap in [look_at_press_page()] + [look_at_table_page(n, u, pl, session_ck)
                                              for n, u, pl in TABLE_PAGES]:
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
