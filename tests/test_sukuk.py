"""BD Govt Investment Sukuk: a product the dashboard held none of.

Fixtures are BB's real pages, saved on 25-Oct-2026 (eleven issues, 53,500 crore).
The rules pinned here are the ones a plausible shortcut would break: columns are
found by what BB calls them (BB's own headers contain typos), a source that moved
fails loudly instead of misreading, and a document BB posted before this existed
is history - announcing it would open the feature by crying wolf.
"""
import datetime
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from engines import announce as A
from fetchers import sukuk as S
from fetchers import source_watch as SW

_FX = os.path.join(_ROOT, "tests", "fixtures")


def _read(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as fh:
        return fh.read()


class TestTheOutstandingTable:
    def test_all_eleven_issues_and_BBs_total(self):
        o = S.parse_outstanding(_read("bgis_outstanding.html"))
        assert len(o["rows"]) == 11 and o["refused"] == {}
        assert sum(r["outstanding_crore"] for r in o["rows"]) == 53500.0

    def test_the_273_day_short_term_issue_is_read_exactly(self):
        rows = {x["isin"]: x for x in S.parse_outstanding(_read("bgis_outstanding.html"))["rows"]}
        r = rows["BDS090905275"]
        assert r["issue_date"] == datetime.date(2026, 6, 29)
        assert r["maturity_date"] == datetime.date(2027, 3, 29)
        assert r["profit_rate_pct"] == 9.36 and r["profit_frequency"] == "on Maturity"
        assert r["outstanding_crore"] == 5500.0

    def test_thousands_separators_and_trailing_space_do_not_corrupt_an_amount(self):
        """BB prints "1,000.00 " for one issue and "8000" for another."""
        assert S._num("1,000.00 ") == 1000.0 and S._num("8000") == 8000.0
        assert S._num("9.51%") == 9.51 and S._num("") is None

    def test_columns_are_mapped_by_header_text_not_position(self):
        """Insert a column BEFORE ISIN: a positional parser reads every field from
        the wrong index and returns plausible wrong numbers."""
        html = _read("bgis_outstanding.html")
        html = html.replace('<th valign="middle" align="center" width="100">SL. No.</th>',
                            "<th>SL. No.</th><th>Note</th>", 1)
        html = html.replace("<tr><td>", "<tr><td>x</td><td>")
        o = S.parse_outstanding(html)
        assert len(o["rows"]) == 11 and o["rows"][0]["isin"].startswith("BDS")
        assert sum(r["outstanding_crore"] for r in o["rows"]) == 53500.0

    def test_a_required_header_that_disappears_fails_loudly(self):
        html = _read("bgis_outstanding.html").replace("Outstanding BDT (Cr.)", "Balance")
        with pytest.raises(SW.StructureChanged):
            S.parse_outstanding(html)

    def test_a_row_that_does_not_make_sense_is_refused_and_counted(self):
        html = _read("bgis_outstanding.html").replace("2026-06-29", "2028-01-01", 1)  # issue after maturity
        o = S.parse_outstanding(html)
        assert len(o["rows"]) == 10 and any("maturity not after issue" in k for k in o["refused"])

    def test_a_page_with_no_table_is_a_structural_error_not_zero_issues(self):
        o = S.parse_outstanding("<html><body>Under maintenance</body></html>")
        assert o["rows"] == [] and o["meta"]["error"]


class TestTheDocuments:
    def test_notices_and_prospectuses_carry_BBs_date_title_and_pdf(self):
        n = S.parse_documents(_read("bgis_notices.html"), "NOTICE")["rows"]
        p = S.parse_documents(_read("bgis_prospectus.html"), "PROSPECTUS")["rows"]
        assert len(n) == 10 and len(p) == 12
        first = n[0]
        assert first["doc_date"] == datetime.date(2026, 6, 23)
        assert first["ref"] == "DMD/257/2026-960"
        assert first["title"] == "Auction Notice for the first Short Term BGIS (273 Days)"
        assert first["url"].endswith("jun232026bgtbe1136.pdf")
        assert first["doc_key"] == "sukuk-jun232026bgtbe1136"

    def test_keys_are_unique_across_both_pages(self):
        n = S.parse_documents(_read("bgis_notices.html"), "NOTICE")["rows"]
        p = S.parse_documents(_read("bgis_prospectus.html"), "PROSPECTUS")["rows"]
        keys = [d["doc_key"] for d in n + p]
        assert len(keys) == len(set(keys))

    def test_a_row_without_a_link_is_refused_not_invented(self):
        html = ('<div class="row-data"><div class="auction_no">X<br>01/01/2026</div>'
                '<div class="auction_title">t</div></div>')
        d = S.parse_documents(html, "NOTICE")
        assert d["rows"] == [] and d["refused"]


def _store():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _run(monkeypatch, session, outstanding, documents):
    import engines.pipeline as P
    monkeypatch.setattr(P, "init_db", lambda: None)
    monkeypatch.setattr(P, "get_session", lambda: session)
    import fetchers.sukuk as F
    monkeypatch.setattr(F, "fetch_sukuk", lambda: {
        "outstanding": outstanding, "documents": documents, "refused": {}, "errors": [], "meta": None})
    return P.run_sukuk_fetch()


def _docs(*keys):
    return [{"doc_key": k, "url": f"https://x/{k}.pdf", "kind": "NOTICE",
             "doc_date": datetime.date(2026, 6, 23), "ref": None, "title": f"Notice {k}"} for k in keys]


class _Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)

        class X:
            returncode, stdout, stderr = 0, "[]", ""
        return X()


class TestStoringAndAnnouncing:
    def test_the_first_run_marks_everything_already_told(self, monkeypatch):
        """BB posted ten notices before this existed; announcing them would open the
        feature by crying wolf, and an alert that does is read once."""
        s = _store()
        _run(monkeypatch, s, [], _docs("sukuk-a", "sukuk-b"))
        n = s.query(dbmod.SukukDocument).filter(dbmod.SukukDocument.notified_utc.is_(None)).count()
        assert n == 0
        assert A.pending(s) == []

    def test_a_document_that_appears_later_is_announced_exactly_once(self, monkeypatch):
        s = _store()
        _run(monkeypatch, s, [], _docs("sukuk-a"))
        _run(monkeypatch, s, [], _docs("sukuk-a", "sukuk-new"))
        p = A.pending(s)
        assert [x["serial"] for x in p] == ["sukuk-new"] and p[0]["category"] == "SUKUK"
        run = _Runner()
        out = A.publish(s, runner=run)
        assert out["opened"] == ["sukuk-new"]
        titles = [c[c.index("--title") + 1] for c in run.calls if c[1:3] == ["issue", "create"]]
        assert len(titles) == 1 and "[sukuk-new]" in titles[0]
        assert A.pending(s) == []          # done only after the issue existed

    def test_a_rerun_does_not_duplicate_or_renotify(self, monkeypatch):
        s = _store()
        _run(monkeypatch, s, [], _docs("sukuk-a"))
        _run(monkeypatch, s, [], _docs("sukuk-a", "sukuk-b"))
        _run(monkeypatch, s, [], _docs("sukuk-a", "sukuk-b"))
        assert s.query(dbmod.SukukDocument).count() == 2

    def test_outstanding_is_upserted_by_isin_not_duplicated(self, monkeypatch):
        s = _store()
        row = {"isin": "BDS000000001", "project": "p", "tenor_label": "5 Y", "contract": "Ijarah",
               "issue_date": datetime.date(2026, 1, 1), "maturity_date": datetime.date(2031, 1, 1),
               "profit_rate_pct": 9.0, "profit_frequency": "Half Yearly", "tradability": "t",
               "outstanding_crore": 1000.0}
        _run(monkeypatch, s, [row], [])
        _run(monkeypatch, s, [{**row, "outstanding_crore": 800.0}], [])
        rows = s.query(dbmod.SukukOutstanding).all()
        assert len(rows) == 1 and rows[0].outstanding_crore == 800.0

    def test_the_digest_names_a_new_sukuk_document(self, monkeypatch):
        s = _store()
        _run(monkeypatch, s, [], _docs("sukuk-a"))
        _run(monkeypatch, s, [], _docs("sukuk-a", "sukuk-new"))
        lines, problems = A.digest_lines(s)
        assert any("sukuk-new" in ln for ln in lines)

    def test_the_weekly_job_runs_the_step(self):
        src = open(os.path.join(_ROOT, "weekly_fetch.py"), encoding="utf-8").read()
        assert "run_sukuk_fetch" in src


class TestABlockedPageIsNotBBRestructuring:
    """The GitHub runner got a 200 with no table. Recording that as 'BB changed its page and
    the parse got worse' is a false alarm about BB and hides that this runner was blocked."""

    def test_a_tiny_page_without_a_table_is_a_transport_error(self, monkeypatch):
        import curl_cffi.requests as cr

        class R:
            status_code, text, url = 200, "<html><body>Please enable JS</body></html>", "u"

            def raise_for_status(self):
                pass
        monkeypatch.setattr(cr, "get", lambda *a, **k: R())
        out = S.fetch_sukuk()
        assert out["outstanding"] == []
        assert out["meta"]["transport_error"] and not out["meta"]["error"]

    def test_the_real_page_still_parses(self, monkeypatch):
        import curl_cffi.requests as cr
        real = _read("bgis_outstanding.html")

        class R:
            status_code, url = 200, "u"
            text = real + (" " * 20000)          # a real page is large

            def raise_for_status(self):
                pass
        monkeypatch.setattr(cr, "get", lambda *a, **k: R())
        assert len(S.fetch_sukuk()["outstanding"]) == 11
