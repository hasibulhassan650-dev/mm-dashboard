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
