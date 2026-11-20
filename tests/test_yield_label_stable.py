"""BB's results page can list ONE auction under two labels (3-Feb-2026, ISIN BD0928101056, 2.60 years left: '2Y' and '5Y').

The writer relabels in place by ISIN, and whichever row was written last used to win - so the label flipped on every re-read, and each flip
made the auction ledger book the same 25,000 mn twice. The label must be the one nearest the bond's own remaining life, whatever the order.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import PrimaryYieldSnapshot
from engines.pipeline import upsert_primary_yield

D = datetime.date


def _row(label, years, remaining="2.60 Yr", isin="BD0928101056"):
    return dict(snapshot_date=D(2026, 2, 28), auction_date=D(2026, 2, 3), issue_date=D(2026, 2, 5), security_type="T_BOND",
                tenor_label=label, tenor_years=years, cutoff_yield_pct=10.4598, offered_bdt_crore=8298.42, accepted_bdt_crore=2500.0,
                isin=isin, reissue_remaining=remaining, original_tenor_label="5Y", sale_value_bdt_crore=2425.59, is_reissue=True)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


@pytest.mark.parametrize("order", [("2Y", "5Y"), ("5Y", "2Y")])
def test_the_label_does_not_depend_on_which_row_is_read_last(order):
    s = _mem()
    years = {"2Y": 2.0, "5Y": 5.0}
    for lab in order * 2:                                   # read twice, as a re-read does
        upsert_primary_yield(s, _row(lab, years[lab]))
        s.commit()
    rows = s.query(PrimaryYieldSnapshot).all()
    assert len(rows) == 1 and rows[0].tenor_label == "2Y", [r.tenor_label for r in rows]


def test_with_no_readable_remaining_life_the_stored_label_stays_instead_of_flipping():
    s = _mem()
    upsert_primary_yield(s, _row("5Y", 5.0, remaining=None))
    s.commit()
    upsert_primary_yield(s, _row("2Y", 2.0, remaining=None))
    s.commit()
    assert s.query(PrimaryYieldSnapshot).one().tenor_label == "5Y"
