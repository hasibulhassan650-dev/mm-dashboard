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
    def test_later_release_replaces_mislabelled_one(self):
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction
        sess = _mem_session()

        wrong = [_row("CB_REPO", 7, 17307.41, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343"),
                 _row("IBLF", 7, 4586.08, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343"),
                 _row("SDF", 1, 2988.0, "ABSORPTION", datetime.date(2026, 8, 6), "05/2026-343")]
        _store_omo_txns(sess, wrong, datetime.datetime.utcnow()); sess.commit()

        correct = [_row("IBLF", 7, 1945.80, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345"),
                   _row("SDF", 1, 3102.0, "ABSORPTION", datetime.date(2026, 8, 9), "05/2026-345")]
        saved, superseded = _store_omo_txns(sess, correct, datetime.datetime.utcnow()); sess.commit()

        got = sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 6)).all()
        insts = sorted(g.instrument for g in got)
        assert superseded == 1
        assert insts == ["IBLF", "SDF"], f"expected only the correction's rows, got {insts}"
        # the wrong release's figures must be gone, not piled on
        assert all(g.instrument != "CB_REPO" for g in got)
        iblf = next(g for g in got if g.instrument == "IBLF")
        assert abs(iblf.accepted_bdt_crore - 1945.80) < 1e-6

    def test_both_releases_in_one_batch_keeps_latest(self):
        # if a single fetch pulls both 343 and 345, only 345 (latest pub) is kept
        from engines.pipeline import _store_omo_txns
        from db import OMOTransaction
        sess = _mem_session()
        batch = [_row("CB_REPO", 7, 17307.41, "INJECTION", datetime.date(2026, 8, 6), "05/2026-343"),
                 _row("IBLF", 7, 1945.80, "INJECTION", datetime.date(2026, 8, 9), "05/2026-345")]
        _store_omo_txns(sess, batch, datetime.datetime.utcnow()); sess.commit()
        got = sess.query(OMOTransaction).filter_by(transaction_date=datetime.date(2026, 8, 6)).all()
        assert sorted(g.instrument for g in got) == ["IBLF"]

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
