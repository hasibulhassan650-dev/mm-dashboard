"""BB's press-release listing: what it announced, and whether we acted on it.

One page carries nearly everything BB publishes, and three failures of this
project were all sitting in it:

  * the OMO links that moved from `a.pdf-file` to `a.pdf-list`, unnoticed for
    eleven days;
  * Bangladesh's first bond buyback, announced 05-Oct-2026 and settled on the
    12th, whose 1,716.14 crore never reached the ladder;
  * the weekly BGIIB (Islamic bond) auctions, of which this project still
    stores no figures.

Keeping the listing is what makes "BB announced something we have no record
of" a question that can be asked at all. Classification is by keyword over
BB's own titles — a deterministic transform with no judgement in it, which is
why it is code and not a model. The titles below are BB's, verbatim.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import BuybackEvent, PressRelease
from fetchers.press import classify, parse_listing, subject_date

D = datetime.date


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng)()


class TestClassificationOfBBsRealTitles:
    def test_the_buyback_result(self):
        assert classify("Press Release regarding the Buyback of 2 Year Treasury bonds "
                        "under Liability Management Operation (LMO)") == "BUYBACK"

    def test_the_buyback_notice_is_also_a_buyback(self):
        """A notice and a result are both buyback news. The watcher tells them
        apart by the word 'notice' — a notice announces, a result moves cash."""
        assert classify("Auction Notice for the buyback of 2 Year Treasury bonds under "
                        "Liability Management Operation (LMO)") == "BUYBACK"

    def test_a_bond_auction_notice_is_a_notice_not_a_result(self):
        """Order matters: this names a BGTB auction and would otherwise be read
        as a result, booking an outflow from an auction that has not happened."""
        assert classify("Auction Notice of 10 years (Re-Issue for Remaining 5.61 Years) "
                        "BGTB to be held on 13 October 2026") == "AUCTION_NOTICE"

    def test_omo(self):
        assert classify("Open Market Operations as on 13 October 2026") == "OMO"

    def test_the_islamic_window(self):
        assert classify("Bangladesh Government Islami Investment Bond (BGIIB) Auction "
                        "Result held on 13 October 2026") == "BGIIB"

    def test_treasury_bills(self):
        assert classify("Treasury Bills Auctions held on 11 October 2026") == "TBILL_RESULT"

    def test_treasury_bonds(self):
        assert classify("10-Year Bangladesh Govt. Treasury Bond Auction (Re-issue) Result "
                        "with 8.0% Coupon") == "TBOND_RESULT"

    def test_an_frtb_is_a_bond_result(self):
        assert classify("03 Year Floating Rate Treasury Bond Auction Result held on "
                        "06 August 2026") == "TBOND_RESULT"

    def test_something_unrelated_keeps_its_title_and_is_marked_unread(self):
        """A category invented for a release nobody recognised is worse than
        admitting the release is unrecognised."""
        assert classify("Bhutan's Central Bank Governor Pays Courtesy Call on "
                        "Bangladesh Bank Governor") == "OTHER"


class TestTheDateAReleaseIsAbout:
    """Not the date it was posted. "Open Market Operations as on 13 October
    2026" went up on the 14th, and storing publication as subject is how a
    one-day lag becomes a permanent off-by-one in everything downstream."""

    def test_as_on(self):
        assert subject_date("Open Market Operations as on 13 October 2026") == D(2026, 10, 13)

    def test_held_on(self):
        assert subject_date("Treasury Bills Auctions held on 11 October 2026") == D(2026, 10, 11)

    def test_a_title_with_no_date_says_so(self):
        assert subject_date("Press Release regarding the Buyback of 2 Year Treasury "
                            "bonds") is None

    def test_an_impossible_date_is_not_invented(self):
        assert subject_date("Auction held on 31 February 2026") is None


class TestTheListingParse:
    _HTML = """
    <table>
      <tr><td><a class="pdf-list" onclick="loadPDFDynamically('https://www.bb.org.bd/mediaroom/press_release/press/pr14328_20261014.pdf')">Open Market Operations as on 13 October 2026</a></td></tr>
      <tr><td><a class="pdf-list" onclick="loadPDFDynamically('https://www.bb.org.bd/mediaroom/press_release/press/pr14322_20261008.pdf')">Press Release regarding the Buyback of 2 Year Treasury bonds</a></td></tr>
      <tr><td><a class="pdf-list" onclick="loadPDFDynamically('https://www.bb.org.bd/mediaroom/press_release/press/malformed.pdf')">Something with no serial</a></td></tr>
    </table>
    """

    def _parse(self):
        from bs4 import BeautifulSoup
        return parse_listing(BeautifulSoup(self._HTML, "lxml"))

    def test_it_reads_the_serial_and_the_publication_date_from_the_filename(self):
        rows = {r["serial"]: r for r in self._parse()["rows"]}
        assert rows["pr14328"]["published_date"] == D(2026, 10, 14)
        assert rows["pr14328"]["subject_date"] == D(2026, 10, 13)

    def test_a_link_it_cannot_key_is_counted_not_dropped_silently(self):
        out = self._parse()
        assert len(out["rows"]) == 2
        assert sum(out["skipped"].values()) == 1

    def test_the_fingerprint_records_the_selector_that_matched(self):
        meta = self._parse()["meta"]
        assert "pdf-list" in (meta["selector"] or "")
        assert meta["rows_parsed"] == 2


class TestTheAnnouncementWatcher:
    """BB announced a buyback twice and nothing noticed either time."""

    def _issues(self, monkeypatch, with_event: bool):
        import validate
        eng, s = _mem()
        s.add(PressRelease(serial="pr14322", published_date=D(2026, 10, 8),
                           title="Press Release regarding the Buyback of 2 Year Treasury "
                                 "bonds under Liability Management Operation (LMO)",
                           url="https://example.invalid/pr14322.pdf", category="BUYBACK"))
        s.add(PressRelease(serial="pr14314", published_date=D(2026, 10, 5),
                           title="Auction Notice for the buyback of 2 Year Treasury bonds",
                           url="https://example.invalid/pr14314.pdf", category="BUYBACK"))
        if with_event:
            s.add(BuybackEvent(isin="BD0926191026", settlement_date=D(2026, 10, 12),
                               accepted_face_crore=1716.14))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        rep = validate.integrity_check()
        return [i for i in rep["issues"] if i.startswith("press")]

    def test_an_announced_buyback_with_no_event_is_a_defect(self, monkeypatch):
        found = self._issues(monkeypatch, with_event=False)
        assert found, "BB announced a buyback and the ladder has nothing — silently"
        assert "pr14322" in found[0]

    def test_recording_the_event_silences_it(self, monkeypatch):
        assert self._issues(monkeypatch, with_event=True) == []

    def test_a_notice_alone_does_not_demand_an_event(self, monkeypatch):
        """A notice announces an auction that has not happened. Only the result
        moves cash, so only the result demands a recorded event."""
        import validate
        eng, s = _mem()
        s.add(PressRelease(serial="pr14314", published_date=D(2026, 10, 5),
                           title="Auction Notice for the buyback of 2 Year Treasury bonds",
                           url="https://example.invalid/x.pdf", category="BUYBACK"))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        assert [i for i in validate.integrity_check()["issues"]
                if i.startswith("press")] == []


class TestTheIslamicWindowParse:
    """BGIIB figures, and BB's own Total line as the check on them.

    Thirteen weekly releases sat captured-but-unparsed, so an entire
    instrument was missing from every ladder, curve and export. The parse is
    straightforward — the releases are in English and tabular — and the guard
    is what matters: BB prints a Total, so every release is checked against
    BB's own arithmetic before it is believed. That guard caught my own
    incomplete regex on its first run, which is exactly what it is for.
    """

    _HEAD = ("Bangladesh Government Islami Investment Bond (BGIIB) Auction Result "
             "held on 13 October 2026\n"
             "PARTICULARS BIDS OFFERED BIDS ACCEPTED\n")

    def _pdf_text(self, body: str) -> str:
        return self._HEAD + body

    def _parse(self, body: str):
        """Parse from text, bypassing pdfplumber."""
        import datetime as _dt

        from fetchers import islamic
        text = self._pdf_text(body)
        # parse_bgiib_pdf takes bytes; exercise the same logic on text by
        # patching the extraction, which is the only PDF-specific step.
        import types
        rows, refused = [], {}
        held = islamic.auction_date_from_text(text)
        assert held == _dt.date(2026, 10, 13)
        # Re-run the row/total logic exactly as parse_bgiib_pdf does.
        import re
        for line in (re.sub(r"\s+", " ", l).strip() for l in text.split("\n")):
            m = islamic._ROW.match(line)
            if m:
                rows.append(m.groupdict())
            elif islamic._TOTAL.match(line):
                refused["total_seen"] = 1
        return rows, refused

    def test_the_two_normal_tenors_parse(self):
        rows, _ = self._parse(
            "3-Month BGIIB 5 670.00 5 670.00 90:10 7.40 7.00-9.50 6.30-8.55\n"
            "6-Month BGIIB 5 313.04 5 313.04 90:10 6.90 5.25-12.00 4.73-10.80\n"
            "Total 10 983.04 10 983.04\n")
        assert len(rows) == 2
        assert rows[0]["months"] == "3" and rows[0]["acc"] == "670.00"
        assert rows[1]["epr"] == "6.90"

    def test_a_single_bid_tenor_has_one_rate_not_a_range(self):
        """The real line that broke it: with one bid there is no range, so BB
        prints the rate once. Demanding "a-b" dropped the line, and because BB
        states a Total the whole release was then refused — correctly."""
        rows, _ = self._parse(
            "3-Month BGIIB 4 450.00 4 450.00 90:10 7.50 7.00-9.50 6.30-8.55\n"
            "6-Month BGIIB 1 100.00 1 100.00 90:10 8.80 9.80 8.82\n"
            "Total 5 550.00 5 550.00 -\n")
        assert len(rows) == 2, "the single-bid tenor was dropped again"
        assert rows[1]["epr_range"] == "8.82"

    def test_the_auction_date_is_when_it_was_held(self):
        """Not when it was published: the 13-Oct auction went up on the 14th,
        and filing it under the 14th puts the Islamic window a day away from
        every series it is compared against."""
        from fetchers.islamic import auction_date_from_text
        assert auction_date_from_text(self._HEAD) == datetime.date(2026, 10, 13)

    def test_a_release_whose_rows_do_not_add_up_is_refused_whole(self):
        """One missing tenor misstates the size of the window, so a partial
        parse is worse than none."""
        from fetchers.islamic import parse_bgiib_pdf
        import io as _io

        # Drive the real function with a stub PDF reader.
        import fetchers.islamic as mod

        class _FakePage:
            def extract_text(self):
                return mod._TEST_TEXT

        class _FakePdf:
            pages = [_FakePage()]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        mod._TEST_TEXT = self._pdf_text(
            "3-Month BGIIB 4 450.00 4 450.00 90:10 7.50 7.00-9.50 6.30-8.55\n"
            "Total 5 550.00 5 550.00\n")
        import pdfplumber
        real_open = pdfplumber.open
        pdfplumber.open = lambda *a, **k: _FakePdf()
        try:
            rep = parse_bgiib_pdf(b"%PDF-fake")
        finally:
            pdfplumber.open = real_open
        assert rep["rows"] == []
        assert any("do not add up" in k for k in rep["refused"]), rep["refused"]
