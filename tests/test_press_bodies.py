"""A press release is downloaded from BB ONCE and its text kept; parsers then run from what we hold.

Why: BB's firewall CAPTCHAs repeated fetches (it did after a few manual reads of the same session), so a parser that
had to re-download its release on every change - or every run - would lock the desk's PC out of the very pages it needs."""
import datetime
import pathlib
import sys
import types

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import db as dbmod
from engines import pipeline as P

D = datetime.date
NOW = datetime.datetime(2026, 11, 21, 12, 0)
SUKUK_TEXT = (pathlib.Path(__file__).parent / "fixtures" / "sukuk_notice_pr14384.txt").read_text(encoding="utf-8")


def _world(monkeypatch, served):
    """In-memory DB with one SUKUK and one AUCTION_NOTICE release; `served` = {url: text or None (a dead link)}."""
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for serial, cat, url, title in (("pr1", "SUKUK", "u1", "Press Release for the 2nd Short-term Bangladesh Government Investment Sukuk Auction"),
                                    ("pr2", "AUCTION_NOTICE", "u2", "Auction Notice")):
        s.add(dbmod.PressRelease(serial=serial, published_date=D(2026, 11, 12), title=title, url=url, category=cat,
                                 first_seen_utc=NOW, last_seen_utc=NOW))
    s.commit()
    calls = []
    sess = types.ModuleType("fetchers.bb_session")
    sess.get_f5_cookies = lambda url: ({}, "ua")

    def get(url, ck, ua):
        calls.append(url)
        t = served[url]
        return b"%PDF-" + (t or "").encode() if t is not None else b"<html>Request Rejected</html>"
    sess.bb_get_bytes = get
    monkeypatch.setitem(sys.modules, "fetchers.bb_session", sess)

    class _Pdf:
        def __init__(self, data): self.t = data[5:].decode()
        def __enter__(self): return types.SimpleNamespace(pages=[types.SimpleNamespace(extract_text=lambda: self.t)])
        def __exit__(self, *a): return False
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda f: _Pdf(f.getvalue()))
    return s, calls


def test_a_release_is_downloaded_once_and_the_sukuk_is_parsed_from_the_stored_text(monkeypatch):
    s, calls = _world(monkeypatch, {"u1": SUKUK_TEXT, "u2": "notice text"})
    r = P.read_sukuk_notices(s, NOW)
    assert r["added"] == 1 and r["errors"] == []
    assert s.execute(text("SELECT body_text FROM press_releases WHERE serial='pr1'")).scalar().startswith("evsjv")
    assert calls == ["u1"], "only the sukuk release was needed"
    P.read_sukuk_notices(s, NOW + datetime.timedelta(hours=1))
    assert calls == ["u1"], "a release already held must never be downloaded again"


def test_other_releases_are_kept_for_later_parsers_a_few_per_run(monkeypatch):
    s, calls = _world(monkeypatch, {"u1": SUKUK_TEXT, "u2": "notice text"})
    r = P.store_press_bodies(s, NOW, categories=("AUCTION_NOTICE",), limit=4)
    assert r["stored"] == 1 and calls == ["u2"]
    assert s.execute(text("SELECT body_text FROM press_releases WHERE serial='pr2'")).scalar() == "notice text"


def test_a_dead_link_is_remembered_and_retried_weekly_not_every_run(monkeypatch):
    s, calls = _world(monkeypatch, {"u1": None, "u2": "x"})
    P.store_press_bodies(s, NOW, categories=("SUKUK",))
    assert calls == ["u1"]
    P.store_press_bodies(s, NOW + datetime.timedelta(days=1), categories=("SUKUK",))
    assert calls == ["u1"], "BB lists it but never serves it: hitting it hourly only adds traffic"
    P.store_press_bodies(s, NOW + datetime.timedelta(days=8), categories=("SUKUK",))
    assert calls == ["u1", "u1"]


def test_the_press_releases_are_read_before_the_sukuk_pages_that_trip_the_chrome_breaker():
    """The PC's Chrome cannot clear F5 on BB's sukuk notice/prospectus pages; two failures disable further launches for the run,
    so releases read AFTER them were never read (body_text stayed empty for days). Order is the fix."""
    import inspect
    src = inspect.getsource(P.run_sukuk_fetch)
    assert src.index("read_sukuk_notices(") < src.index("fetch_sukuk()") and src.index("store_press_bodies(") < src.index("fetch_sukuk()")
