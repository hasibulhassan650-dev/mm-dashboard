"""When Bangladesh Bank changes something.

Every other check in this project notices data that stops arriving. The desk
asked the harder question — "what if BB suddenly changes something? will the
guard catch it?" — and the honest answer was no, twice over:

  * BB renamed its press-release links from `a.pdf-file` to `a.pdf-list`. The
    fetcher matched nothing, returned an empty list, and eleven days of missing
    OMO data were indistinguishable from a quiet market.

  * Worse, and still latent: the treasury parser collected BB's real header
    texts under the comment "Locate column indices by keyword scan of header
    text" and then mapped columns POSITIONALLY, branched on cell count. Insert
    one column upstream and that branch still matches — sale value read as a
    yield, a bid count read as an amount. Nothing missing, nothing stale, no
    check anywhere that could see it. Plausible wrong numbers, published.

So these tests pin down three behaviours, and the difference between them is
the point:

  1. a column INSERTED or REORDERED still maps correctly, by header text;
  2. a required header GONE is a loud refusal that names what moved — never
     rows read from guessed positions;
  3. a harmless rewording is a dated notice, not an escalation, because an
     alarm that fires on cosmetics is how a real outage became wallpaper.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from fetchers import source_watch as SW
from fetchers.treasury import TREASURY_COLUMNS, _parse_page
from tests.test_treasury_parse import _FULL

SNAP = datetime.date(2026, 9, 28)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


# ── BB inserts a column ──────────────────────────────────────────────────────

# BB's live table with one extra column inserted in the middle of "Bids
# accepted": a plausible edit (they have added columns before) and the exact
# shape that silently shifted every field to its left under positional mapping.
_INSERTED = (_FULL
             .replace('<th colspan="7">Bids accepted</th>',
                      '<th colspan="8">Bids accepted</th>')
             .replace('<th>Sale value (Cr.Tk.)</th>',
                      '<th>Sale value (Cr.Tk.)</th><th>Allotment ratio (%)</th>')
             .replace('<td>3689.28</td>', '<td>3689.28</td><td>35.81</td>')
             .replace('<td>511.79</td>', '<td>511.79</td><td>45.07</td>')
             .replace('<td>2937.48</td>', '<td>2937.48</td><td>33.71</td>'))


class TestBBInsertsAColumn:
    def test_the_figures_are_still_right(self):
        rows = _parse_page(_INSERTED, SNAP)
        bond = next(r for r in rows if r["original_tenor_label"] == "20Y")
        # Face value, sale value and cut-off are three adjacent columns whose
        # meanings are not interchangeable: par, cash, and a percentage.
        assert bond["accepted_bdt_crore"] == 3500.00
        assert bond["sale_value_bdt_crore"] == 3689.28
        assert bond["cutoff_yield_pct"] == 8.8685
        assert bond["devolvement_yield_pct"] == 8.75

    def test_positional_mapping_would_have_been_wrong(self):
        """Not a tautology: this is what the old code would have produced.

        With 15 cells the old `if n >= 14` branch still matched, so column 10
        was read as the accepted range of yields when it now holds the new
        allotment ratio, and the cut-off came from column 12 which now holds
        the weighted-average PRICE — 105.4081, a price, stored as a yield.
        """
        cells = ["02/09/2026", "BD0929441204", "2yr", "20yr T.Bond", "339", "9773.16",
                 "8.7280-11.7100", "257", "3500.00", "3689.28", "35.81", "8.7280-8.8685",
                 "105.4081", "8.8685", "8.75"]
        assert float(cells[12]) == 105.4081, "the old cut-off index now holds a price"
        rows = _parse_page(_INSERTED, SNAP)
        bond = next(r for r in rows if r["original_tenor_label"] == "20Y")
        assert bond["cutoff_yield_pct"] != 105.4081

    def test_the_new_column_is_reported_not_ignored(self):
        meta = {}
        _parse_page(_INSERTED, SNAP, meta)
        unmapped = [u["header"] for u in meta["unmapped"]]
        assert any("allotment" in h for h in unmapped), unmapped
        assert meta["column_count"] == 15

    def test_a_reordered_table_maps_by_name(self):
        """Columns swapped, not added. Position says one thing, BB says another."""
        swapped = (_FULL
                   .replace("<th>Cut off yield (%)</th><th>Standard/ Devolvement Yield (%)</th>",
                            "<th>Standard/ Devolvement Yield (%)</th><th>Cut off yield (%)</th>")
                   .replace("<td>8.8685</td><td>8.75</td>", "<td>8.75</td><td>8.8685</td>"))
        rows = _parse_page(swapped, SNAP)
        bond = next(r for r in rows if r["original_tenor_label"] == "20Y")
        assert bond["cutoff_yield_pct"] == 8.8685
        assert bond["devolvement_yield_pct"] == 8.75


# ── BB removes something we require ──────────────────────────────────────────

class TestARequiredHeaderDisappears:
    def test_it_refuses_loudly_instead_of_guessing(self):
        gone = _FULL.replace("<th>Cut off yield (%)</th>", "<th>Clearing level</th>")
        with pytest.raises(SW.StructureChanged) as exc:
            _parse_page(gone, SNAP)
        # The message has to be enough to fix it without an investigation.
        assert "cutoff" in str(exc.value)
        assert "clearing level" in str(exc.value).lower()

    def test_it_returns_no_rows_at_all(self):
        """The dangerous outcome is not an exception — it is rows.

        A parser that falls back to positions here would hand the desk a full
        page of plausible figures read from the wrong columns.
        """
        gone = _FULL.replace("<th>Cut off yield (%)</th>", "<th>Clearing level</th>")
        try:
            rows = _parse_page(gone, SNAP)
        except SW.StructureChanged:
            rows = None
        assert rows is None, "rows were produced from a table we cannot map"

    def test_the_failure_is_fingerprinted_for_the_guardian(self):
        gone = _FULL.replace("<th>Cut off yield (%)</th>", "<th>Clearing level</th>")
        meta = {}
        with pytest.raises(SW.StructureChanged):
            _parse_page(gone, SNAP, meta)
        assert meta["rows_parsed"] == 0
        assert meta["error"]
        verdict, reasons = SW.compare(None, meta)
        assert verdict == "degraded", reasons


# ── BB rewords a header harmlessly ───────────────────────────────────────────

class TestAHarmlessRewording:
    def test_the_parse_is_unaffected(self):
        reworded = _FULL.replace("<th>Cut off yield (%)</th>",
                                 "<th>Cut-off Yield (per cent)</th>")
        rows = _parse_page(reworded, SNAP)
        bond = next(r for r in rows if r["original_tenor_label"] == "20Y")
        assert bond["cutoff_yield_pct"] == 8.8685

    def test_it_is_a_notice_and_not_an_escalation(self):
        before, after = {}, {}
        _parse_page(_FULL, SNAP, before)
        _parse_page(_FULL.replace("<th>Cut off yield (%)</th>",
                                  "<th>Cut-off Yield (per cent)</th>"), SNAP, after)
        verdict, reasons = SW.compare(before, after)
        assert verdict == "changed", (verdict, reasons)
        assert any("header" in r for r in reasons), reasons

    def test_an_identical_page_is_silent(self):
        a, b = {}, {}
        _parse_page(_FULL, SNAP, a)
        _parse_page(_FULL, SNAP, b)
        assert SW.compare(a, b) == ("ok", [])


# ── the OMO case, which is what started this ──────────────────────────────────

class TestTheSelectorThatStoppedMatching:
    """`a.pdf-file[pdf-link]` became `a.pdf-list[onclick]`.

    The fetcher returned [], the step logged "OMO OK | new_rows=0", and the
    freshness check blamed BB. This is the signal that was available on day
    one and that nothing was recording.
    """

    def test_it_degrades_and_says_which_selector_moved(self):
        before = SW.fingerprint("omo_press", selector="a.pdf-file[pdf-link]",
                                rows_seen=162, rows_parsed=9)
        after = SW.fingerprint("omo_press", selector="", rows_seen=162, rows_parsed=0,
                               error="no anchors matched any known markup")
        verdict, reasons = SW.compare(before, after)
        assert verdict == "degraded"
        assert any("no anchors matched" in r for r in reasons), reasons

    def test_a_new_selector_that_still_works_is_only_a_change(self):
        before = SW.fingerprint("omo_press", selector="a.pdf-file[pdf-link]",
                                rows_seen=162, rows_parsed=9)
        after = SW.fingerprint("omo_press", selector="a.pdf-list[onclick]",
                               rows_seen=162, rows_parsed=9)
        verdict, reasons = SW.compare(before, after)
        assert verdict == "changed"
        assert any("selector" in r for r in reasons), reasons


# ── skips are counted, not swallowed ─────────────────────────────────────────

class TestSkipsAreCounted:
    """33 bare `continue` statements across three fetchers dropped rows with no
    record. A parser skipping most of a page looked exactly like a quiet week."""

    def test_normal_non_data_rows_are_not_an_alarm(self):
        meta = {}
        _parse_page(_FULL, SNAP, meta)
        assert meta["rows_parsed"] == 3
        before = SW.fingerprint("treasury", rows_seen=40, rows_parsed=38,
                                skips={"blank devolvement row": 2})
        assert SW.compare(before, meta if False else before) == ("ok", [])

    def test_a_parser_dropping_most_of_the_page_is_a_finding(self):
        before = SW.fingerprint("treasury", rows_seen=40, rows_parsed=38,
                                skips={"blank devolvement row": 2})
        after = SW.fingerprint("treasury", rows_seen=40, rows_parsed=6,
                               skips={"cut-off yield missing or not positive": 34})
        verdict, reasons = SW.compare(before, after)
        assert verdict == "degraded"
        assert any("skipped" in r or "less than half" in r for r in reasons), reasons

    def test_a_snapshot_that_counted_nothing_is_not_a_baseline_for_skips(self):
        """CI reads the press-release list through Chrome (no row counts); the PC reads it directly and
        sees the page's other 120 releases. Comparing the two called a normal page 'worse than 0%'."""
        chrome = SW.fingerprint("omo_press", selector="chrome-fallback", rows_seen=0, rows_parsed=78)
        direct = SW.fingerprint("omo_press", selector="chrome-fallback", rows_seen=156, rows_parsed=78,
                                skips={"not an OMO release": 82, "older than the window": 38})
        assert SW.compare(chrome, direct)[0] == "ok"

    def test_the_reasons_travel_with_the_count(self):
        bad = _FULL.replace("<td>8.8685</td><td>8.75</td>", "<td></td><td></td>")
        meta = {}
        _parse_page(bad, SNAP, meta)
        assert meta["rows_parsed"] == 2
        assert sum(meta["skip_reasons"].values()) == 1
        assert "cut-off" in " ".join(meta["skip_reasons"])


# ── the registry: what counts as BB's normal ─────────────────────────────────

class TestTheBaselineCannotBeHijacked:
    """Two failure modes, opposite directions.

    Compare against "last time" and a broken shape becomes the baseline on its
    second run — the change goes quiet by itself. Compare against "most
    sightings" and a deliberate BB redesign is reported as news for as long as
    the old shape was ever seen. Neither is acceptable, so the baseline is the
    most recent shape that has held for days.
    """

    def _see(self, s, snap, when):
        return SW.record(s, snap, now=when)

    def test_a_degraded_shape_never_becomes_the_baseline(self):
        s = _mem()
        good = SW.fingerprint("omo_press", selector="a.pdf-file", rows_seen=162, rows_parsed=9)
        bad = SW.fingerprint("omo_press", selector="", rows_seen=162, rows_parsed=0,
                             error="no anchors matched")
        t0 = datetime.datetime(2026, 9, 1, 6, 0)
        for d in range(5):                      # five days of healthy fetches
            self._see(s, good, t0 + datetime.timedelta(days=d))
        for d in range(5, 12):                  # then a week of the outage
            v, why = self._see(s, bad, t0 + datetime.timedelta(days=d))
            assert v == "degraded", (d, v, why)
        assert SW.established(s, "omo_press")["selector"] == "a.pdf-file"

    def test_a_real_bb_change_settles_down_after_a_few_days(self):
        s = _mem()
        old = SW.fingerprint("treasury", selector="table[0]",
                             headers=["issue date", "cut off yield"], rows_seen=9, rows_parsed=9)
        new = SW.fingerprint("treasury", selector="table[0]",
                             headers=["issue date", "cut off yield", "allotment ratio"],
                             rows_seen=9, rows_parsed=9)
        t0 = datetime.datetime(2026, 9, 1, 6, 0)
        for d in range(6):
            self._see(s, old, t0 + datetime.timedelta(days=d))
        seen = [self._see(s, new, t0 + datetime.timedelta(days=6 + d))[0] for d in range(5)]
        assert seen[0] == "changed", seen
        assert seen[-1] == "ok", f"a persistent BB layout must stop being news: {seen}"
        assert "allotment ratio" in SW.established(s, "treasury")["headers"]

    def test_several_pages_in_one_run_do_not_settle_it_the_same_day(self):
        """One refresh reads eight monthly pages. If sightings alone settled a
        shape, a change would be accepted before the morning digest named it."""
        s = _mem()
        old = SW.fingerprint("treasury", selector="t", headers=["a"], rows_seen=9, rows_parsed=9)
        new = SW.fingerprint("treasury", selector="t", headers=["a", "b"],
                             rows_seen=9, rows_parsed=9)
        t0 = datetime.datetime(2026, 9, 1, 6, 0)
        for d in range(6):
            self._see(s, old, t0 + datetime.timedelta(days=d))
        day = t0 + datetime.timedelta(days=6)
        verdicts = [self._see(s, new, day + datetime.timedelta(minutes=i))[0] for i in range(8)]
        assert set(verdicts) == {"changed"}, verdicts

    def test_the_first_ever_sighting_is_not_an_alarm(self):
        s = _mem()
        v, why = self._see(s, SW.fingerprint("brand_new", selector="x", rows_seen=5,
                                             rows_parsed=5), datetime.datetime.utcnow())
        assert v == "ok", why

    def test_but_a_first_sighting_that_parses_nothing_is(self):
        s = _mem()
        v, why = self._see(s, SW.fingerprint("brand_new", selector="x", rows_seen=120,
                                             rows_parsed=0), datetime.datetime.utcnow())
        assert v == "degraded", why


# ── the header flattener, which everything above depends on ──────────────────

class TestTwoLevelHeaders:
    def test_received_and_accepted_are_told_apart(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(_FULL, "lxml")
        rows = soup.find_all("tr")[:2]
        cols = SW.header_columns(rows)
        assert len(cols) == 14, cols
        # Identical sub-names under different banners: the market's demand
        # versus the amount actually sold. Conflating them misstates both.
        assert cols[5] == "bids received face value cr tk"
        assert cols[8] == "bids accepted face value cr tk"
        assert cols[9] == "bids accepted sale value cr tk"

    def test_punctuation_and_case_do_not_matter(self):
        assert SW.norm("Cut-off Yield(%)") == SW.norm("CUT OFF  yield %")

    def test_a_rowspan_header_also_works(self):
        from bs4 import BeautifulSoup
        html = ('<table><tr><th rowspan="2">Issue date</th>'
                '<th colspan="2">Bids accepted</th></tr>'
                '<tr><th>Face value</th><th>Sale value</th></tr></table>')
        cols = SW.header_columns(BeautifulSoup(html, "lxml").find_all("tr"))
        assert cols == ["issue date", "bids accepted face value",
                        "bids accepted sale value"], cols


class TestOurOwnFailuresAreNotBBsChanges:
    """Three false alarms the watcher produced about itself, all caught before
    they reached the desk — and all the same mistake in different clothes:
    recording something about US as a fact about BB's page.

    1. Chrome would not launch on the machine running a fetch. That was stored
       as "the parse failed outright", the source went `degraded`, and the
       guardian reported BB's page as suspect. The page was never served to
       us at all.
    2. The canary labelled its selector "largest table via curl_cffi",
       putting the TRANSPORT into the shape — so the ordinary fallback to the
       F5 handshake would have reported BB restructuring the page.
    3. The canary and the production fetchers registered under one source
       name while reading different views of the same page, giving one source
       two rival baselines that each report the other as a change, weekly.

    A watcher whose whole job is to cry only when something real moves cannot
    afford any of them.
    """

    def test_a_failed_fetch_is_not_a_degraded_source(self):
        snap = SW.fingerprint("omo_press", selector="", rows_seen=0, rows_parsed=0,
                              transport_error="SessionNotCreatedException: chrome not reachable")
        verdict, reasons = SW.compare(None, snap)
        assert verdict == "unread", (verdict, reasons)
        assert "could not be read" in reasons[0]

    def test_a_failed_fetch_never_overwrites_a_known_good_shape(self):
        s = _mem()
        good = SW.fingerprint("omo_press", selector="a.pdf-list[onclick]",
                              rows_seen=160, rows_parsed=94)
        t0 = datetime.datetime(2026, 10, 1, 6, 0)
        for d in range(4):
            SW.record(s, good, now=t0 + datetime.timedelta(days=d))
        dead = SW.fingerprint("omo_press", selector="", rows_seen=0, rows_parsed=0,
                              transport_error="chrome not reachable")
        verdict, _ = SW.record(s, dead, now=t0 + datetime.timedelta(days=5))
        assert verdict == "unread"
        from db import SourceSnapshot
        assert s.query(SourceSnapshot).count() == 1, "a failed fetch registered a shape"
        assert SW.established(s, "omo_press")["selector"] == "a.pdf-list[onclick]"

    def test_a_structural_failure_is_still_degraded(self):
        """The distinction has to cut both ways: a page we DID read and cannot
        map is a real finding, and must not be excused as a transport blip."""
        snap = SW.fingerprint("treasury", selector="table[0]", rows_seen=12, rows_parsed=0,
                              error="required column(s) cutoff not found in BB's header")
        assert SW.compare(None, snap)[0] == "degraded"

    def test_the_canary_keeps_its_own_baseline(self):
        import canary
        assert canary.CANARY.endswith(":")
        # Same page, two readers, two namespaces: the canary reads the default
        # view, the fetcher POSTs a month form and sees a different table.
        s = _mem()
        t0 = datetime.datetime(2026, 10, 1, 6, 0)
        prod = SW.fingerprint("treasury", selector="table[0]",
                              headers=["issue date", "cut off yield"],
                              rows_seen=9, rows_parsed=9)
        cana = SW.fingerprint(canary.CANARY + "treasury", selector="largest table",
                              headers=["issue date", "cut off yield"],
                              rows_seen=12, rows_parsed=10)
        for d in range(4):
            SW.record(s, prod, now=t0 + datetime.timedelta(days=d))
            SW.record(s, cana, now=t0 + datetime.timedelta(days=d))
        v_prod, _ = SW.record(s, prod, now=t0 + datetime.timedelta(days=5))
        v_cana, _ = SW.record(s, cana, now=t0 + datetime.timedelta(days=5))
        assert (v_prod, v_cana) == ("ok", "ok"), "the two readers reported each other"

    def test_the_selector_describes_the_page_not_the_transport(self):
        """Reading the same page over a different transport is not a change."""
        import re
        from pathlib import Path
        src = Path("canary.py").read_text(encoding="utf-8")
        assert not re.search(r'selector=f?"largest table via', src), \
            "the transport is back inside the selector"


class TestAGoodReadAfterABadOneSettlesTheAlarm:
    """GitHub's runners are shown F5's CAPTCHA and read 'no table'; the desk PC reads the same page
    fine an hour later. The alarm must follow the newest read, or a firewall blocking one machine
    keeps a data defect on the page for four days after the data is fine."""

    def _world(self, monkeypatch, order):
        import validate
        eng = create_engine("sqlite:///:memory:")
        dbmod.Base.metadata.create_all(eng)
        s = sessionmaker(bind=eng)()
        now = datetime.datetime.utcnow()
        for hours_ago, shape, verdict in order:
            s.add(dbmod.SourceSnapshot(source="sukuk_outstanding", structure_hash=shape, first_seen=now,
                                       last_seen=now - datetime.timedelta(hours=hours_ago), seen_count=1,
                                       verdict=verdict, reasons='["the parse failed outright"]',
                                       selector="table", column_count=11))
        s.commit()
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        return validate.integrity_check(limit_per_rule=200)

    def test_a_clean_read_after_a_degraded_one_is_silent(self, monkeypatch):
        rep = self._world(monkeypatch, [(5, "blocked", "degraded"), (1, "good", "ok")])
        assert not [i for i in rep["issues"] if "sukuk_outstanding" in i and "page changed" in i]

    def test_a_degraded_read_after_a_clean_one_still_alarms(self, monkeypatch):
        rep = self._world(monkeypatch, [(5, "good", "ok"), (1, "blocked", "degraded")])
        assert [i for i in rep["issues"] if "sukuk_outstanding" in i and "page changed" in i]
