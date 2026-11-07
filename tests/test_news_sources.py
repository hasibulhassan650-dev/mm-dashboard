"""The news-fetching ENGINE (Liquidity Watch phase 2): sitemap sources and the alarms that watch them.

Nobody fetches news by hand for this project; the engine fetches on a schedule and must notice for itself when a source
goes quiet. So these tests use made-up sitemaps and a fake clock: no network, and every alarm is shown to fire.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import NewsArticle, NewsSourceRun
from engines import news_triage as T
from fetchers import news as N
from fetchers import source_watch as SW

NOW = datetime.datetime(2026, 11, 8, 12, 0, 0)

_GN = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
 <url><loc>https://www.example.net/business/banking/news/bb-releases-tk-3000cr-4821?ref=home</loc>
  <news:news><news:publication><news:name>X</news:name></news:publication>
   <news:publication_date>2026-11-08T10:15:00+06:00</news:publication_date>
   <news:title>BB releases Tk 3,000cr as refinance</news:title></news:news></url>
 <url><loc>https://www.example.net/sports/cricket/news/tigers-win-1</loc>
  <news:news><news:publication_date>2026-11-08T09:00:00+06:00</news:publication_date>
   <news:title>Tigers win</news:title></news:news></url>
 <url><loc>https://www.example.net/business/economy/news/excess-liquidity-reaches-record-9912</loc>
  <lastmod>2026-11-07T18:00:00Z</lastmod></url>
</urlset>"""


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


class TestASitemapIsRead:
    def test_only_the_business_section_is_kept_with_its_title_and_a_utc_date(self):
        got = N.parse_news_sitemap(_GN, section=r"/business/")
        assert [g["url"] for g in got] == ["https://www.example.net/business/banking/news/bb-releases-tk-3000cr-4821",
                                           "https://www.example.net/business/economy/news/excess-liquidity-reaches-record-9912"]
        assert got[0]["title"] == "BB releases Tk 3,000cr as refinance"
        assert got[0]["published_utc"] == datetime.datetime(2026, 11, 8, 4, 15), "+06:00 is converted to UTC"

    def test_a_page_with_no_title_gets_one_from_its_slug_not_an_empty_one(self):
        """New Age's sitemap has no titles; the keyword filter needs words. The slug is a stand-in, never a headline."""
        got = N.parse_news_sitemap(_GN, section=r"/business/")
        assert got[1]["title"] == "excess liquidity reaches record"
        assert N.slug_title("https://x/post/economy/12345-bb-cuts-crr") == "bb cuts crr"

    def test_the_limit_caps_a_huge_sitemap(self):
        assert len(N.parse_news_sitemap(_GN, limit=1)) == 1

    def test_a_date_without_a_zone_is_local_time_when_the_source_says_so(self):
        """Samakal prints Dhaka time with no zone: 19:14 is 13:14 UTC, not 19:14 UTC (6 hours ahead of the truth)."""
        assert N._when("2026-11-08T19:14:00", 6) == datetime.datetime(2026, 11, 8, 13, 14)
        assert N._when("2026-11-08T19:14:00") == datetime.datetime(2026, 11, 8, 19, 14)


class TestTheRegistry:
    def test_bangla_sources_are_registered_but_not_read_until_the_guards_exist(self):
        """A Bangla amount (কোটি, লাখ) and a Bangla request cannot be checked yet, and an unchecked amount is how a
        wrong figure gets stored. Switching one on must be a deliberate edit that removes `enabled: False`."""
        bn = [s for s in N.NEWS_SOURCES if s.get("lang") == "bn"]
        assert bn and all(s.get("enabled") is False and s.get("why") for s in bn)
        assert not [s for s in N.active_sources() if s.get("lang") == "bn"]

    def test_the_engine_reads_every_active_source_and_no_other(self):
        assert {s["key"] for s in N.active_sources()} <= {s["key"] for s in N.NEWS_SOURCES}
        assert len({s["key"] for s in N.NEWS_SOURCES}) == len(N.NEWS_SOURCES), "keys are unique"
        assert all(s["url"].startswith("https://") for s in N.NEWS_SOURCES)

    def test_a_sitemap_shrinking_with_the_news_day_is_not_a_layout_change(self):
        """A sitemap lists the last two days, so its size swings with the newsroom like a feed's does."""
        full = SW.fingerprint("news:tbs_gn", selector="news sitemap url", rows_seen=60, rows_parsed=60)
        thin = SW.fingerprint("news:tbs_gn", selector="news sitemap url", rows_seen=9, rows_parsed=9)
        empty = SW.fingerprint("news:tbs_gn", selector="news sitemap url", rows_seen=0, rows_parsed=0,
                               error="the sitemap holds no page in the section /economy/")
        assert SW.compare(full, thin) == ("ok", [])
        assert SW.compare(full, empty)[0] == "degraded"


class TestEveryRunRecordsEverySource:
    def test_an_unreachable_source_leaves_a_row_the_fingerprint_could_not_store(self):
        """A 403 is a transport failure, which source_watch deliberately never stores - so a feed blocked for a week left
        no trace anywhere. The run table records it."""
        eng, s = _mem()
        srcs = [{"key": "good", "label": "g", "url": "u"}, {"key": "blocked", "label": "b", "url": "u"}]

        def listing(src):
            if src["key"] == "blocked":
                return {"items": [], "error": "HTTP 403", "meta": None}
            return {"items": [{"url": "https://x/1", "title": "t", "summary": "", "published_utc": NOW}], "error": None, "meta": None}
        T.run(s, None, NOW, sources=srcs, fetch_listing=listing, fetch_body=lambda u: None)
        rows = {r.source: r for r in s.query(NewsSourceRun).all()}
        assert rows["good"].ok and rows["good"].items == 1 and rows["good"].new_items == 1 and rows["good"].newest_utc == NOW
        assert not rows["blocked"].ok and rows["blocked"].error == "HTTP 403"
        assert rows["good"].machine in ("pc", "github")

    def test_a_run_that_dies_later_keeps_what_it_already_read(self):
        """One commit at the very end meant a timeout lost the whole run's articles."""
        eng, s = _mem()

        def boom(session, client, now, fetch_body, **kw):
            raise RuntimeError("provider hung")
        orig = T.triage_pending
        T.triage_pending = boom
        try:
            try:
                T.run(s, None, NOW, sources=[{"key": "good", "label": "g", "url": "u"}],
                      fetch_listing=lambda src: {"items": [{"url": "https://x/9", "title": "t", "summary": "",
                                                            "published_utc": NOW}], "error": None, "meta": None},
                      fetch_body=lambda u: None)
            except RuntimeError:
                pass
        finally:
            T.triage_pending = orig
        s.rollback()
        assert dbmod.NewsArticle  # noqa: B018
        fresh = sessionmaker(bind=eng)()
        assert fresh.query(NewsSourceRun).count() == 1 and fresh.query(NewsArticle).count() == 1


class TestTheAlarmsWatchTheEngine:
    """validate.integrity_check reads news_source_runs: the watch is judged by its own record, not by what it found."""

    def _issues(self, monkeypatch, eng, key="news-source"):
        import validate
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check(limit_per_rule=200)
        return [i for i in rep["issues"] if i.startswith(key)], [n for n in rep["notices"] if n.startswith(key)]

    def _run(self, s, key, ago_h, ok=True, newest_ago_h=1, err=None, machine="pc"):
        t = datetime.datetime.utcnow() - datetime.timedelta(hours=ago_h)
        s.add(NewsSourceRun(source=key, machine=machine, run_utc=t, ok=ok, items=5 if ok else 0, new_items=0,
                            newest_utc=(datetime.datetime.utcnow() - datetime.timedelta(hours=newest_ago_h)) if ok else None,
                            error=err))
        s.commit()

    def _all_ok(self, s, skip=()):
        for src in N.active_sources():
            if src["key"] not in skip:
                self._run(s, src["key"], 1)

    def test_all_sources_read_recently_is_quiet(self, monkeypatch):
        eng, s = _mem()
        self._all_ok(s)
        assert self._issues(monkeypatch, eng) == ([], [])

    def test_a_source_no_machine_has_read_for_a_day_is_a_defect_naming_the_last_error(self, monkeypatch):
        eng, s = _mem()
        key = N.active_sources()[0]["key"]
        self._all_ok(s, skip=(key,))
        self._run(s, key, 40, ok=True)
        self._run(s, key, 2, ok=False, err="HTTP 403", machine="github")
        issues, _ = self._issues(monkeypatch, eng)
        assert len(issues) == 1 and key in issues[0] and "HTTP 403" in issues[0]

    def test_a_site_that_blocks_github_is_fine_while_the_pc_reads_it(self, monkeypatch):
        """Some sites refuse a datacenter address; that is a fact about GitHub, not a dead source."""
        eng, s = _mem()
        key = N.active_sources()[0]["key"]
        self._all_ok(s, skip=(key,))
        self._run(s, key, 1, ok=True, machine="pc")
        self._run(s, key, 0.5, ok=False, err="HTTP 403", machine="github")
        assert self._issues(monkeypatch, eng) == ([], [])

    def test_a_source_that_never_worked_in_a_day_is_a_defect(self, monkeypatch):
        eng, s = _mem()
        key = N.active_sources()[0]["key"]
        self._all_ok(s, skip=(key,))
        self._run(s, key, 30, ok=False, err="HTTP 404")
        self._run(s, key, 1, ok=False, err="HTTP 404")
        issues, _ = self._issues(monkeypatch, eng)
        assert len(issues) == 1 and "never read by any machine" in issues[0]

    def test_a_feed_that_answers_but_froze_is_a_notice_not_a_defect(self, monkeypatch):
        """Still serving the same old items for ever looked 'ok' to the layout fingerprint."""
        eng, s = _mem()
        key = N.active_sources()[0]["key"]
        self._all_ok(s, skip=(key,))
        self._run(s, key, 1, ok=True, newest_ago_h=100)
        issues, notices = self._issues(monkeypatch, eng)
        assert issues == [] and len(notices) == 1 and "frozen" in notices[0]

    def test_a_weekend_of_silence_is_not_a_frozen_feed(self, monkeypatch):
        eng, s = _mem()
        key = N.active_sources()[0]["key"]
        self._all_ok(s, skip=(key,))
        self._run(s, key, 1, ok=True, newest_ago_h=60)          # a Friday-Saturday gap
        assert self._issues(monkeypatch, eng) == ([], [])

    def test_the_heartbeat_is_the_last_run_not_the_newest_article(self, monkeypatch):
        """A watch that ran and found nothing new is alive; one that stopped is not, however fresh its last article."""
        eng, s = _mem()
        s.add(NewsArticle(url="https://x/a", source="s", title="t", fetched_utc=datetime.datetime.utcnow(),
                          triage_status="skipped"))
        s.commit()
        self._run(s, "dailystar_business", 9)                    # last RUN was 9h ago
        issues, _ = self._issues(monkeypatch, eng, key="news-watch")
        assert issues and "has not run for" in issues[0]
        eng2, s2 = _mem()
        s2.add(NewsArticle(url="https://x/a", source="s", title="t",
                           fetched_utc=datetime.datetime.utcnow() - datetime.timedelta(hours=20), triage_status="skipped"))
        s2.commit()
        self._run(s2, "dailystar_business", 1)                   # ran an hour ago, nothing new for a day
        assert self._issues(monkeypatch, eng2, key="news-watch") == ([], [])
