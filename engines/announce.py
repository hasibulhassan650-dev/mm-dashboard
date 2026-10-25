"""Tell the desk when Bangladesh Bank announces something that matters.

BB announced the country's first bond buyback on 05-Oct-2026 and its result on
the 8th. Nothing here reacted to either: the releases were on BB's front page
and, once the listing was stored, in our own database - and 1,716.14 crore
reached the market on the 12th with no line in the ladder. Storing the listing
made the question answerable; this makes the answer arrive on its own.

The channel is a GitHub issue per announcement. That is deliberate, not a
shortcut: the repository already emails its owner on a new issue, the integrity
gate and the digest already use the same channel, and it needs no new secret or
service. One issue per release, titled with BB's own serial, so a rerun finds
the existing issue instead of opening a second.

What is worth an email is a short list - a buyback, an auction notice, an
Islamic-window result, a policy-rate change, a Sukuk. The 60-odd routine OMO
releases are NOT announced: an inbox that fills with them teaches you to stop
reading, which is the failure the rest of this system exists to avoid. They
appear in the daily digest and on /press instead.

Three guards, each there because the opposite failure is bad:
  * a release is marked notified only AFTER its issue exists, so a failed
    notification is retried on the next run rather than silently dropped;
  * only releases first seen in the last few days are announced, so switching
    this on never replays history;
  * a hard cap per run, so a bug or a re-listing cannot send fifty emails.
"""
import datetime
import json
import logging
import subprocess
from typing import Callable, Dict, List, Optional, Sequence

log = logging.getLogger("announce")

# Worth an email. OMO and the routine result categories are deliberately absent.
HIGH_SIGNAL = ("BUYBACK", "AUCTION_NOTICE", "BGIIB", "SUKUK", "POLICY")

LABEL = "bb-announcement"
MAX_AGE_DAYS = 3
CAP_PER_RUN = 8

_WHY = {
    "BUYBACK": "A government bond BUYBACK moves cash into the market on its settlement "
               "day and shrinks the bond's outstanding. The ladder carries it only once "
               "a buyback event is recorded.",
    "AUCTION_NOTICE": "An auction notice sets the size, tenor and date of a coming "
                      "auction - the cash outflow on its settlement day.",
    "BGIIB": "An Islamic-window (BGIIB) auction result: bids, accepted amount and the "
             "expected profit rate for the 3- and 6-month tenors.",
    "SUKUK": "A BGIS (government sukuk) notice or prospectus: an issue that will pull "
             "cash out of the market on its settlement day. Outstanding issues and "
             "their maturities are on the Islamic page.",
    "POLICY": "A policy-rate announcement. Rates are re-read from BB's homepage "
              "daily; this is the heads-up.",
}


def _sources():
    """The tables that can announce something.

    Each source is a table of things BB (or later, the news watch) published, a
    key column that is unique and goes in the issue title, and the SQL that picks
    the ones worth an email. The select always yields the same six names so the
    rest of this module never needs to know which table an item came from.
    """
    marks = ",".join(f"'{c}'" for c in HIGH_SIGNAL)          # constants, not user input
    return (
        {"table": "press_releases", "key": "serial",
         "select": "serial, published_date, subject_date, title, url, category, 'BB announced' AS prefix",
         "where": f"category IN ({marks}) AND notified_utc IS NULL AND first_seen_utc >= :since",
         "order": "published_date, serial"},
        {"table": "sukuk_documents", "key": "doc_key",
         "select": "doc_key AS serial, doc_date AS published_date, NULL AS subject_date, "
                   "title, url, 'SUKUK' AS category, 'BB announced' AS prefix",
         "where": "notified_utc IS NULL AND first_seen_utc >= :since",
         "order": "doc_date, doc_key"},
        # Events read from the news (engines/liquidity_news.py): only those that MATTER
        # and that BB's own tables do not already hold. The key is built, so the
        # issue title carries "[ev-12]" and a rerun finds its own issue.
        {"table": "liquidity_events", "key": "('ev-' || CAST(id AS TEXT))",
         "select": "('ev-' || CAST(id AS TEXT)) AS serial, COALESCE(event_date, announced_date) AS "
                   "published_date, NULL AS subject_date, "
                   "(kind || ': ' || COALESCE(summary, '')) AS title, '' AS url, kind AS category, "
                   "'Liquidity news' AS prefix",
         "where": "matters IS TRUE AND redundant_with IS NULL AND notified_utc IS NULL "
                  "AND first_seen_utc >= :since",
         "order": "first_seen_utc, id"},
    )


_FIELDS = ("serial", "published_date", "subject_date", "title", "url", "category", "prefix")


def pending(session, now: datetime.datetime = None,
            max_age_days: int = MAX_AGE_DAYS, cap: int = CAP_PER_RUN) -> List[Dict]:
    """Un-notified high-signal items first seen recently, oldest first."""
    from sqlalchemy import text
    now = now or datetime.datetime.utcnow()
    since = now - datetime.timedelta(days=max_age_days)
    out: List[Dict] = []
    for s in _sources():
        rows = session.execute(text(
            f"SELECT {s['select']} FROM {s['table']} WHERE {s['where']} ORDER BY {s['order']}"),
            {"since": since}).fetchall()
        out += [dict(zip(_FIELDS, r)) for r in rows]
    out.sort(key=lambda x: (str(x["published_date"] or ""), x["serial"]))
    if len(out) > cap:
        log.warning("%d announcements pending, announcing the %d most recent",
                    len(out), cap)
        out = out[-cap:]
    return out


def mark_notified(session, serials: Sequence[str], now: datetime.datetime = None) -> int:
    from sqlalchemy import text
    now = now or datetime.datetime.utcnow()
    n = 0
    for sr in serials:
        for s in _sources():      # keys never collide across tables (pr..., sukuk-...)
            n += session.execute(text(
                f"UPDATE {s['table']} SET notified_utc = :now WHERE {s['key']} = :s "
                "AND notified_utc IS NULL"), {"now": now, "s": sr}).rowcount
    return n


def baseline(session, now: datetime.datetime = None) -> int:
    """Mark everything stored so far as already announced.

    Run once when this is switched on. Without it the first run would announce
    whatever was first seen in the last few days - releases the desk has long
    since read - and an alert that opens by crying wolf is read once.
    """
    from sqlalchemy import text
    now = now or datetime.datetime.utcnow()
    n = 0
    for s in _sources():
        n += session.execute(text(
            f"UPDATE {s['table']} SET notified_utc = :now WHERE notified_utc IS NULL"),
            {"now": now}).rowcount
    return n


def first_run(session) -> bool:
    """True when releases are stored but none has ever been announced."""
    from sqlalchemy import text
    total = session.execute(text("SELECT COUNT(*) FROM press_releases")).scalar() or 0
    sent = session.execute(text(
        "SELECT COUNT(*) FROM press_releases WHERE notified_utc IS NOT NULL")).scalar() or 0
    return total > 0 and sent == 0


def digest_lines(session, now: datetime.datetime = None, hours: int = 24):
    """(lines, problems) for the daily digest: what BB published, and any
    high-signal release that should have been announced but was not."""
    from sqlalchemy import text
    now = now or datetime.datetime.utcnow()
    new = session.execute(text(
        "SELECT serial, published_date, category, title FROM press_releases "
        "WHERE first_seen_utc >= :s ORDER BY published_date DESC, serial DESC"),
        {"s": now - datetime.timedelta(hours=hours)}).fetchall()
    lines = [f"**New from BB in the last {hours}h:**"]
    routine = [r for r in new if r[2] not in HIGH_SIGNAL]
    for sr, pub, cat, title in new:
        if cat in HIGH_SIGNAL:
            lines.append(f"- {cat.replace('_', ' ').title()} · {str(title)[:110]} "
                         f"({str(pub)[:10]}, {sr})")
    if routine:
        lines.append(f"- {len(routine)} routine release(s) (OMO / results) - see /press")
    docs = session.execute(text(
        "SELECT doc_key, doc_date, kind, title FROM sukuk_documents "
        "WHERE first_seen_utc >= :s ORDER BY doc_date DESC"),
        {"s": now - datetime.timedelta(hours=hours)}).fetchall()
    for key, d, kind, title in docs:
        lines.append(f"- BGIS Sukuk {str(kind).lower()} · {str(title)[:110]} ({str(d)[:10]}, {key})")
    if not new and not docs:
        lines.append("- nothing new")
    lines.append("")
    # Stored 2h+ ago, still un-announced: a failed send, or a re-categorised release.
    stuck = []
    for s in _sources():
        where = s["where"].replace("first_seen_utc >= :since",
                                   "first_seen_utc >= :a AND first_seen_utc < :b")
        stuck += session.execute(text(
            f"SELECT {s['select']} FROM {s['table']} WHERE {where}"),
            {"a": now - datetime.timedelta(days=7),
             "b": now - datetime.timedelta(hours=2)}).fetchall()
    problems = []
    if stuck:
        problems.append(f"{len(stuck)} BB announcement(s) never notified")
        lines.append("**Never announced:** " + ", ".join(r[0] for r in stuck[:8]) + "\n")
    return lines, problems


def news_digest_lines(session, now: datetime.datetime = None, hours: int = 24):
    """(lines, problems): liquidity news for the digest, and the two ways the watch can
    silently stop - it has not read anything for 6h, or articles pile up waiting for Claude."""
    from sqlalchemy import text
    now = now or datetime.datetime.utcnow()
    problems: List[str] = []
    out = [f"**Liquidity news not in BB's data (last {hours}h):**"]
    rows = session.execute(text(
        "SELECT kind, direction, amount_crore, status, counterparty, summary FROM liquidity_events "
        "WHERE first_seen_utc >= :s AND matters IS TRUE AND redundant_with IS NULL "
        "ORDER BY first_seen_utc DESC"), {"s": now - datetime.timedelta(hours=hours)}).fetchall()
    for kind, direction, amt, status, cp, summ in rows:
        amount = "amount not stated" if amt is None else f"{float(amt):,.0f} cr"
        out.append(f"- {kind.replace('_', ' ').title()} ({direction.lower()}) · {amount}"
                   f"{' · ' + status.lower() if status else ''} · {cp or 'counterparty not stated'} · {str(summ)[:110]}")
    if not rows:
        out.append("- nothing new")
    last = session.execute(text("SELECT MAX(fetched_utc) FROM news_articles")).scalar()
    if last is None or (now - _as_dt(last)).total_seconds() > 6 * 3600:
        problems.append("the liquidity news watch has not run for 6h+")
        out.append("**The news watch has not read anything for 6h or more.**")
    waiting = session.execute(text(
        "SELECT COUNT(*) FROM news_articles WHERE triage_status IN ('untriaged', 'error') "
        "AND fetched_utc < :b"), {"b": now - datetime.timedelta(hours=2)}).scalar() or 0
    if waiting > 20:
        problems.append(f"{waiting} news articles are waiting for Claude (key missing or failing)")
        out.append(f"**{waiting} articles are waiting for Claude triage** - ANTHROPIC_API_KEY missing, "
                   f"or the model calls are failing.")
    out.append("")
    return out, problems


def _as_dt(v) -> datetime.datetime:
    if isinstance(v, datetime.datetime):
        return v
    return datetime.datetime.fromisoformat(str(v)[:19])


def issue_title(rel: Dict) -> str:
    # The serial is what a rerun searches for; it must be in the title verbatim.
    return f"{rel.get('prefix') or 'BB announced'}: {rel['title'][:100]} [{rel['serial']}]"


def news_issue_body(session, rel: Dict) -> str:
    """The body for a news-derived event: amount, direction, status, the quote, every
    source, and an explicit statement that it is not BB data and not in the ladder."""
    from sqlalchemy import text
    ev_id = int(str(rel["serial"]).split("-", 1)[1])
    r = session.execute(text(
        "SELECT kind, direction, amount_crore, status, channel, counterparty, summary, "
        "evidence_quote, sources_json, event_date, confidence FROM liquidity_events WHERE id = :i"),
        {"i": ev_id}).fetchone()
    if r is None:
        return f"Liquidity news event {rel['serial']}"
    kind, direction, amt, status, channel, cp, summ, quote, srcs, ev_date, conf = r
    try:
        links = "\n".join(f"- [{s.get('source')}]({s.get('url')}) - {s.get('title')}"
                           for s in json.loads(srcs or "[]"))
    except ValueError:
        links = ""
    amount = "amount not stated" if amt is None else f"**{float(amt):,.0f} crore**"
    return (f"**{kind.replace('_', ' ').title()}** - {direction.lower()} - {amount}"
            f"{' - ' + status.lower() if status else ''}{' - ' + channel.lower() if channel else ''}\n\n"
            f"{summ}\n\n> {quote}\n\n"
            f"Counterparty: {cp or 'not stated'} - event date: {ev_date or 'not stated'} - "
            f"model confidence {float(conf or 0):.0%}\n\n**Sources**\n{links}\n\n"
            f"_Read from the news, not from Bangladesh Bank's data, and **not included in any ladder "
            f"total**. The quote above is verbatim from the first source and the amount was re-read "
            f"from it by code. Opened automatically; close it when you have read it._")


def issue_body(rel: Dict) -> str:
    when = rel.get("subject_date") or rel.get("published_date")
    return (f"**{rel['category'].replace('_', ' ').title()}** - published "
            f"{rel.get('published_date')}"
            + (f", about {when}" if when and when != rel.get("published_date") else "")
            + f"\n\n{_WHY.get(rel['category'], '')}\n\n"
              f"[BB's release (PDF)]({rel['url']}) · BB serial `{rel['serial']}`\n\n"
              f"_Opened automatically by the data refresh. Close it when you have read it; "
              f"it will not be opened again._")


def _gh(args: Sequence[str], runner: Callable = subprocess.run):
    return runner(["gh", *args], capture_output=True, text=True, timeout=60)


def publish(session, runner: Callable = subprocess.run,
            now: datetime.datetime = None) -> Dict:
    """Open one issue per pending announcement; mark each ONLY after it exists."""
    out = {"pending": 0, "opened": [], "already_open": [], "failed": []}
    items = pending(session, now=now)
    out["pending"] = len(items)
    if not items:
        return out

    _gh(["label", "create", LABEL, "--color", "1D76DB", "--force",
         "--description", "Something Bangladesh Bank announced that the desk should see"],
        runner)

    # One listing of our own labelled issues, not a search per release: GitHub's
    # search index is eventually consistent and would miss an issue created a
    # minute ago. If the listing fails, send nothing - never create blind.
    try:
        listed = _gh(["issue", "list", "--label", LABEL, "--state", "all",
                      "--limit", "1000", "--json", "title"], runner)
        if listed.returncode != 0:
            raise RuntimeError((listed.stderr or "").strip()[:160])
        known = " ".join(i.get("title", "") for i in json.loads(listed.stdout or "[]"))
    except Exception as exc:
        out["failed"].append(f"could not list existing issues: {exc}")
        return out

    done: List[str] = []
    for rel in items:
        sr = rel["serial"]
        try:
            if f"[{sr}]" in known:
                out["already_open"].append(sr)       # opened by an earlier run
                done.append(sr)
                continue
            made = _gh(["issue", "create", "--title", issue_title(rel),
                        "--body", (news_issue_body(session, rel) if str(rel["serial"]).startswith("ev-")
                                   else issue_body(rel)), "--label", LABEL], runner)
            if made.returncode == 0:
                out["opened"].append(sr)
                done.append(sr)
            else:
                out["failed"].append(f"{sr}: {(made.stderr or '').strip()[:160]}")
        except Exception as exc:
            out["failed"].append(f"{sr}: {type(exc).__name__} {exc}")

    if done:
        mark_notified(session, done, now=now)
        session.commit()
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Announce new BB releases as GitHub issues")
    ap.add_argument("action", choices=("publish", "list", "baseline"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from db import get_session, init_db
    init_db()      # the refresh step may have died before migrating notified_utc
    s = get_session()
    try:
        if a.action == "publish" and first_run(s):
            # Switched on for the first time: what is stored is history, not news.
            n = baseline(s)
            s.commit()
            print(f"first run: {n} stored release(s) marked as already announced")
            return 0
        if a.action == "list":
            print(json.dumps(pending(s), default=str, indent=2))
        elif a.action == "baseline":
            n = baseline(s)
            s.commit()
            print(f"{n} stored release(s) marked as already announced")
        else:
            res = publish(s)
            print(json.dumps(res, indent=2))
            # A failed notification must be visible in the job, not swallowed.
            return 1 if res["failed"] else 0
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
