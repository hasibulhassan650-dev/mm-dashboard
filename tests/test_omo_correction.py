"""
tests/test_omo_correction.py — BB OMO press-release corrections.

WHY: BB publishes each OMO release with a 1-day lag ('as on' = previous
working day). Occasionally it mislabels the date (publishes same-day) and
issues a corrected release days later re-stating the same operation date with
the right figures. The store layer must treat the LATEST-published release for
an operation date as authoritative — otherwise the wrong and corrected figures
pile onto one day and every OMO/liquidity number for that date is wrong.
Real case: serial 05/2026-343 (pub 06-Aug, 'as on 06-Aug', no lag) was
superseded by 05/2026-345 (pub 09-Aug, 'as on 06-Aug').
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetchers.omo import _parse_pub_meta, _parse_date_from_text


class TestPubMetaParsing:
    def test_normal_release_has_one_day_lag(self):
        s = "Serial No- 05/2026-342 Date: 04-08-2026\nOpen Market Operations as on 03 August 2026"
        pub, serial = _parse_pub_meta(s)
        assert serial == "05/2026-342"
        assert pub == datetime.date(2026, 8, 4)
        assert _parse_date_from_text(s) == datetime.date(2026, 8, 3)
        assert (pub - _parse_date_from_text(s)).days == 1

    def test_mislabelled_release_has_no_lag(self):
        # 343: published same day as the operation date it claims — the anomaly
        s = "Serial No- 05/2026-343 Date: 06-08-2026\nOpen Market Operations as on 06 August 2026"
        pub, serial = _parse_pub_meta(s)
        ason = _parse_date_from_text(s)
        assert serial == "05/2026-343"
        assert pub <= ason   # no publication lag -> provisional/suspect

    def test_correction_publishes_later(self):
        s = "Serial No- 05/2026-345 Date: 09-08-2026\nOpen Market Operations as on 06 August 2026"
        pub, serial = _parse_pub_meta(s)
        assert serial == "05/2026-345"
        assert pub == datetime.date(2026, 9 - 9 + 8, 9)  # 2026-08-09
        assert (pub - _parse_date_from_text(s)).days == 3


def _mem_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db as dbmod
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _row(instr, tenor, acc, direction, pub, serial, ason=datetime.date(2026, 8, 6)):
    return {"transaction_date": ason, "maturity_date": ason + datetime.timedelta(days=tenor),
            "instrument": instr, "tenor_label": f"{tenor}D", "tenor_days": tenor,
            "accepted_bdt_crore": acc, "maturity_bdt_crore": 0.0, "rate_pct": None,
            "rate_range": None, "direction": direction, "source_pdf": f"pr_{serial}.pdf",
            "source_pub_date": pub, "source_serial": serial}


class TestSupersession:
    def test_genuine_correction_restates_same_operation(self):
        # A REAL correction re-states the SAME operation (CB_REPO stays present)
        # with the right figures → the later release supersedes on its own date.
        # Contrast with TestMislabelGuard, where the later release DROPS CB_REPO.
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction
        sess = _mem_session()

        wrong = [_row("CB_REPO", 7, 17307.41, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343"),
                 _row("IBLF", 7, 4586.08, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343")]
        _store_omo_txns(sess, wrong, datetime.datetime.utcnow()); sess.commit()

        correct = [_row("CB_REPO", 7, 15000.00, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345"),
                   _row("IBLF", 7, 1945.80, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345")]
        saved, superseded = _store_omo_txns(sess, correct, datetime.datetime.utcnow()); sess.commit()

        got = sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 6)).all()
        assert superseded == 1
        assert sorted(g.instrument for g in got) == ["CB_REPO", "IBLF"], "latest correction's rows only"
        cb = next(g for g in got if g.instrument == "CB_REPO")
        assert abs(cb.accepted_bdt_crore - 15000.00) < 1e-6, "corrected figure, not the old one"
        # a genuine correction is NOT re-homed to a Tuesday
        assert sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 4)).count() == 0

    def test_both_releases_in_one_batch_rehomes_cbrepo(self):
        # if a single fetch pulls both 343 (CB Repo, older) and 345 (no CB Repo),
        # the genuine 345 stays on its date AND the CB Repo is re-homed to its
        # Tuesday — never silently dropped by the keep-latest filter.
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction
        sess = _mem_session()
        batch = [_row("CB_REPO", 7, 17307.41, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343"),
                 _row("IBLF", 7, 1945.80, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345")]
        _store_omo_txns(sess, batch, datetime.datetime.utcnow()); sess.commit()
        thu = sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 6)).all()
        tue = sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 4)).all()
        assert sorted(g.instrument for g in thu) == ["IBLF"], "genuine 06-Aug op kept on its date"
        assert any(g.instrument == "CB_REPO" for g in tue), "CB Repo re-homed to its Tuesday, not lost"

    def test_older_release_never_regresses_a_correction(self):
        # if the correction is already stored, re-seeing the old release must not undo it
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction
        sess = _mem_session()
        _store_omo_txns(sess, [_row("IBLF", 7, 1945.80, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345")],
                        datetime.datetime.utcnow()); sess.commit()
        _store_omo_txns(sess, [_row("CB_REPO", 7, 17307.41, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343")],
                        datetime.datetime.utcnow()); sess.commit()
        got = sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 6)).all()
        assert sorted(g.instrument for g in got) == ["IBLF"], "older release must not regress the correction"


class TestInstrumentParsing:
    """A new BB product must NEVER be silently mislabelled as an existing one.
    Real case (09-Aug-2026): MLS 28-Days and SDF 1-Day were both being absorbed
    into IBLF's block, poisoning IBLF's numbers and hiding an absorption as an
    injection."""

    def _parse(self, text):
        import datetime
        from fetchers.omo import _parse_via_text
        return _parse_via_text(text, datetime.date(2026, 8, 9), "pr_test.pdf")

    def test_mls_and_sdf_not_swallowed_by_iblf(self):
        text = (
            "Open Market Operations as on 09 August 2026\n"
            "IBLF 7-Days 2,839.24 2,839.24 3.00-7.00 -2,220.99 618.25\n"
            "MLS 28-Days 1,340.00 1,340.00 5.25 0.00 1,340.00\n"
            "AR 7-Days 1,289.42 1,289.42 9.50 -2,218.06 -928.63\n"
            "AR 180-Days 465.53 465.53 9.50 -1,918.43 -1,452.90\n"
            "Sub-Total 8,148.22 8,148.22 -6,357.47 1,790.74\n"
            "SDF 1-Day -5,011.00 -5,011.00 7.50 3,103.91 -1,907.09\n"
        )
        by = {(r["instrument"], r["tenor_days"]): r for r in self._parse(text)}
        assert ("MLS", 28) in by, "MLS mislabelled"
        assert ("SDF", 1) in by and by[("SDF", 1)]["direction"] == "ABSORPTION"
        assert ("IBLF", 7) in by and abs(by[("IBLF", 7)]["accepted_bdt_crore"] - 2839.24) < 0.01
        assert ("AR", 180) in by
        # IBLF must NOT have picked up the 28D (MLS) or 1D (SDF) rows
        assert ("IBLF", 28) not in by and ("IBLF", 1) not in by

    def test_unknown_instrument_captured_under_own_name(self):
        text = (
            "Open Market Operations as on 09 August 2026\n"
            "CB Repo 7-Days 100.00 100.00 9.50 0.00 100.00\n"
            "NEWFAC 30-Days 250.00 250.00 6.00 0.00 250.00\n"
            "SDF 1-Day -80.00 -80.00 7.50 0.00 -80.00\n"
        )
        rows = self._parse(text)
        insts = {r["instrument"] for r in rows}
        assert "NEWFAC" in insts, "unknown instrument lost / mislabelled"
        assert ("CB_REPO", 30) not in {(r["instrument"], r["tenor_days"]) for r in rows}

    def test_wrapped_instrument_name_not_split_into_fragment(self):
        # BB wraps a long name across lines: "CB Repo (Finetuning" / a data row
        # rendered "for maintaining 1-Day …" / "reserve)". The fragment
        # "for maintaining" must NOT become an instrument (FOR_MAINTAINING) and
        # clobber the CB Repo it belongs to (real case, pr14255, 31-Aug-2026).
        text = (
            "Open Market Operations as on 31 August 2026\n"
            "CB Repo (Finetuning\n"
            "for maintaining 1-Day 7,519.44 7,519.44 9.50 0.00 7,519.44\n"
            "reserve)\n"
            "IBLF 7-Days 8,058.21 8,058.21 3.00 0.00 8,058.21\n"
            "SDF 1-Day -2,891.00 -2,891.00 7.50 3,410.70 519.70\n"
        )
        rows = self._parse(text)
        by = {(r["instrument"], r["tenor_days"]): r for r in rows}
        assert "FOR_MAINTAINING" not in {r["instrument"] for r in rows}, "wrapped-name fragment stored as an instrument"
        assert ("CB_REPO", 1) in by and abs(by[("CB_REPO", 1)]["accepted_bdt_crore"] - 7519.44) < 0.01
        assert by[("CB_REPO", 1)]["direction"] == "INJECTION"


class TestMislabelGuard:
    """Aug-2026: BB stamped the 04-Aug (Tuesday) CB Repo operation with the
    header 'as on 06 August' — the same date as the genuine 06-Aug operation.
    Superseding by date silently DELETED the Tuesday CB Repo. The guard re-homes
    the CB_REPO-bearing release to the Tuesday it belongs to, on either fetch
    order; a genuine correction (same op, keeps CB_REPO) still supersedes."""

    THU = datetime.date(2026, 8, 6)   # Thursday — the contested 'as on' date
    TUE = datetime.date(2026, 8, 4)   # the Tuesday the CB Repo really belongs to

    def _mislabel(self):   # 04-Aug op, has CB Repo, published 06-Aug, mis-stamped as-on 06-Aug
        return [_row("CB_REPO", 7, 14571.0, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343", ason=self.THU),
                _row("SDF", 1, 3000.0, "ABSORPTION", datetime.date(2026, 8, 6), "05/2026-343", ason=self.THU)]

    def _genuine(self):    # real 06-Aug op, NO CB Repo, published later 09-Aug
        return [_row("IBLF", 7, 1945.8, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345", ason=self.THU),
                _row("SDF", 1, 3102.0, "ABSORPTION", datetime.date(2026, 8, 9), "05/2026-345", ason=self.THU)]

    def _assert_split(self, sess):
        from db import OMOTransaction
        tue = sess.query(OMOTransaction).filter_by(transaction_date=self.TUE).all()
        thu = sess.query(OMOTransaction).filter_by(transaction_date=self.THU).all()
        assert any(r.instrument == "CB_REPO" and abs(r.accepted_bdt_crore - 14571.0) < 1e-6 for r in tue), \
            "CB Repo must be preserved on its Tuesday, never deleted by supersession"
        assert not any(r.instrument == "CB_REPO" for r in thu), "no CB Repo on the genuine 06-Aug date"
        assert any(r.instrument == "IBLF" for r in thu), "genuine 06-Aug operation kept on its own date"

    def test_cbrepo_preserved_when_genuine_release_arrives_later(self):
        from engines.pipeline import _store_omo_txns
        sess = _mem_session()
        _store_omo_txns(sess, self._mislabel(), datetime.datetime.utcnow()); sess.commit()
        _store_omo_txns(sess, self._genuine(),  datetime.datetime.utcnow()); sess.commit()
        self._assert_split(sess)

    def test_cbrepo_preserved_when_it_arrives_after_the_genuine_one(self):
        # reverse fetch order: the older CB_REPO release must NOT be dropped as a
        # regression — it re-homes to the Tuesday just the same.
        from engines.pipeline import _store_omo_txns
        sess = _mem_session()
        _store_omo_txns(sess, self._genuine(),  datetime.datetime.utcnow()); sess.commit()
        _store_omo_txns(sess, self._mislabel(), datetime.datetime.utcnow()); sess.commit()
        self._assert_split(sess)

    def test_genuine_correction_with_cbrepo_still_supersedes(self):
        # both releases carry CB Repo (same operation, corrected figures) → normal
        # supersession, latest wins on its own date, nothing re-homed to Tuesday.
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction
        sess = _mem_session()
        _store_omo_txns(sess, [_row("CB_REPO", 7, 17307.41, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343", ason=self.THU)],
                        datetime.datetime.utcnow()); sess.commit()
        _store_omo_txns(sess, [_row("CB_REPO", 7, 1945.80, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345", ason=self.THU)],
                        datetime.datetime.utcnow()); sess.commit()
        assert sess.query(OMOTransaction).filter_by(transaction_date=self.TUE).count() == 0, \
            "a genuine correction must not be re-homed to a Tuesday"
        thu = sess.query(OMOTransaction).filter_by(transaction_date=self.THU).all()
        assert len(thu) == 1 and abs(thu[0].accepted_bdt_crore - 1945.80) < 1e-6, \
            "latest correction wins on its own date"

    def test_no_lag_release_rehomed_to_the_traded_day_with_no_omo(self):
        # Sep-2026 (pr14268 vs pr14269): BB stamped the 03-Sep operation
        # 'as on 06 September' and published it the SAME day (zero lag), while the
        # genuine 06-Sep release published 07-Sep. Keying on the date alone
        # deleted one of two real operations. The no-lag release must be re-homed
        # to the day the market traded with no OMO (03-Sep). Instrument-agnostic:
        # neither release carries a CB Repo, so the older guard could not see it.
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction, CallMoneyRate
        SUN = datetime.date(2026, 9, 6)   # the 'as on' date both releases claim
        THU = datetime.date(2026, 9, 3)   # traded (call money printed), no OMO
        sess = _mem_session()
        sess.add(CallMoneyRate(trade_date=THU)); sess.commit()

        genuine = [_row("IBLF", 7, 11918.86, "INJECTION", datetime.date(2026, 9, 7), "05/2026-404", ason=SUN)]
        _store_omo_txns(sess, genuine, datetime.datetime.utcnow()); sess.commit()
        nolag = [_row("IBLF", 7, 1307.63, "INJECTION", datetime.date(2026, 9, 6), "05/2026-403", ason=SUN)]
        _store_omo_txns(sess, nolag, datetime.datetime.utcnow()); sess.commit()

        thu = sess.query(OMOTransaction).filter_by(transaction_date=THU).all()
        sun = sess.query(OMOTransaction).filter_by(transaction_date=SUN).all()
        assert any(abs(r.accepted_bdt_crore - 1307.63) < 1e-6 for r in thu), \
            "the no-lag (mis-dated) operation must be re-homed to 03-Sep, not dropped"
        assert len(sun) == 1 and abs(sun[0].accepted_bdt_crore - 11918.86) < 1e-6, \
            "the genuine 06-Sep operation must stay on its own date"


class TestReleaseTypeGuard:
    """BB's press feed carries more than OMO. A Treasury-bill auction release
    parsed as OMO turned its table header 'OF BILLS AUCTIONED' into an
    instrument (OF_BILLS_AUCTIONED), which the integrity gate then flags as
    unknown and fails every refresh run. Only a real OMO release may be parsed."""

    def test_treasury_bill_auction_release_is_rejected(self):
        from fetchers.omo import _is_omo_release
        auction = (
            "Bangladesh Bank\nPress Release\n"
            "Treasury Bills Auctions held on 06 September 2026\n"
            "PARTICULARS AMOUNT TO BE BIDS OFFERED BIDS ACCEPTED\n"
            "OF BILLS AUCTIONED\n"
        )
        assert not _is_omo_release(auction), \
            "a T-bill auction release must never be parsed as OMO"

    def test_genuine_omo_release_is_accepted(self):
        from fetchers.omo import _is_omo_release
        assert _is_omo_release(
            "Open Market Operations as on 06 September 2026\n"
            "IBLF 7-Days 11,918.86 11,918.86 3.00 0.00 11,918.86\n"
        )

    def test_wrapped_column_header_is_never_an_instrument(self):
        # 17-Jun-2026 (pr14126): the column header "Amount Amount" wrapped onto its
        # own line where the parser expected a product name, so the IBLF operation
        # was stored under the instrument AMOUNT_AMOUNT. Header vocabulary must
        # never become a product; the row keeps the real instrument.
        text = (
            "Open Market Operations as on 17 June 2026\n"
            "IBLF\n"
            "Amount Amount\n"
            "7-Days 1,109.42 1,109.42 4.00 -716.61 392.81\n"
            "SDF 1-Day -2,000.00 -2,000.00 7.50 0.00 -2,000.00\n"
        )
        from fetchers.omo import _parse_via_text
        rows = _parse_via_text(text, datetime.date(2026, 6, 17), "pr14126.pdf")
        insts = {r["instrument"] for r in rows}
        assert "AMOUNT_AMOUNT" not in insts, "a wrapped column header was stored as an instrument"
        by = {(r["instrument"], r["tenor_days"]): r for r in rows}
        assert ("IBLF", 7) in by and abs(by[("IBLF", 7)]["accepted_bdt_crore"] - 1109.42) < 0.01


class TestRateFingerprint:
    """The rate BB prints identifies the facility unambiguously. When the parser's
    block attribution slips, a row inherits a neighbouring instrument's name —
    Mar–Jun 2026 had 55 such rows, including 13 SDF ABSORPTIONS stored as AR
    INJECTIONS (sign errors in net liquidity) and the 15-Jun IBLF stored as
    CB_REPO that produced the 5,703→13,622 outstanding jump. The rate must win."""

    def _parse(self, text, d):
        from fetchers.omo import _parse_via_text
        return _parse_via_text(text, d, "pr_test.pdf")

    def test_collapsed_block_is_recovered_from_rates(self):
        # 14-May-2026 (pr14079): every row printed under "AR"
        text = ("Open Market Operations as on 14 May 2026\n"
                "AR 7-Days 155.00 155.00 4.00-5.37 -95.07 59.93\n"      # Islamic range → IBLF
                "AR 14-Days 250.00 250.00 4.50 -413.63 -163.63\n"       # fixed ≤5     → IBLF
                "AR 180-Days 83.75 83.75 10.00 0.00 83.75\n"            # policy rate  → AR (kept)
                "AR 1-Day 78.59 78.59 11.50 -51.75 26.84\n"             # ceiling o/n  → SLF
                "AR 1-Day -997.00 -997.00 7.50 5896.21 4899.21\n")      # floor o/n    → SDF absorption
        got = {(r["instrument"], r["tenor_days"]): r["direction"] for r in self._parse(text, datetime.date(2026, 5, 14))}
        assert got == {("IBLF", 7): "INJECTION", ("IBLF", 14): "INJECTION", ("AR", 180): "INJECTION",
                       ("SLF", 1): "INJECTION", ("SDF", 1): "ABSORPTION"}, got

    def test_islamic_range_under_cb_repo_label_is_iblf(self):
        # 15-Jun-2026 (pr14120): the IBLF line stored as CB_REPO — the exact row
        # behind the user's 22-Jun jump. CB Repo never prints a profit range.
        text = ("Open Market Operations as on 15 June 2026\n"
                "CB Repo 7-Days 4,799.94 4,799.94 3.00-5.37 -8,505.39 -3,705.45\n")
        r = self._parse(text, datetime.date(2026, 6, 15))[0]
        assert r["instrument"] == "IBLF" and r["direction"] == "INJECTION"

    def test_consistent_labels_are_left_alone(self):
        text = ("Open Market Operations as on 16 June 2026\n"
                "CB Repo 7-Days 1,000.00 1,000.00 9.50 0.00 1,000.00\n"
                "IBLF 7-Days 500.00 500.00 3.00-5.25 0.00 500.00\n"
                "SDF 1-Day -200.00 -200.00 7.50 0.00 -200.00\n")
        got = {r["instrument"] for r in self._parse(text, datetime.date(2026, 6, 16))}
        assert got == {"CB_REPO", "IBLF", "SDF"}
