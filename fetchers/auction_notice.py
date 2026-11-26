"""Read a BB 'Auction Notice of <n> years (Re-Issue for Remaining <x> Years) BGTB to be held on <date>' press release.

The notice states what is actually being offered: the face value (crore), the ISIN being re-issued, its coupon and maturity. The
auction calendar carries a PLANNED amount, so a notice that differs from it is a change the ladder does not know about. The PDF is in the
legacy Bijoy Bangla font (its words extract as noise); the title is English and the figures are plain digits beside fixed ASCII-looking
tokens, which is all that is anchored on. Anything that does not read cleanly returns None - never a half-read notice.
"""
import datetime
import re
from typing import Dict, Optional

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"], 1)}
_HELD = re.compile(r"held\s+on\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.I)
_REMAIN = re.compile(r"Remaining\s+(\d+(?:\.\d+)?)\s*years?", re.I)
_FRTB = re.compile(r"FRTB", re.I)
_FACE = re.compile(r"(\d[\d,]*\.\d\d)\s*\S+\s+UvKvi\s+AwfwnZ")           # '2,750.00 <crore> Takar nominal (Face Value)'
_ISIN = re.compile(r"\b(BD[0-9A-Z]{10})\b")
_COUPON = re.compile(r"(\d+\.\d+)\s*%")
GRID = (2, 5, 10, 15, 20)


def tenor_class(remaining_years: float, frtb: bool) -> str:
    """The calendar's label for a re-issue: BB classifies by REMAINING life onto 2/5/10/15/20 (an FRTB keeps its own label)."""
    if frtb:
        return "3Y_FRTB"
    return f"{min(GRID, key=lambda g: abs(g - remaining_years))}Y"


def parse_auction_notice(title: str, body: str) -> Optional[Dict]:
    held, rem = _HELD.search(title or ""), _REMAIN.search(title or "")
    if not held or not rem:
        return None
    mon = _MONTHS.get(held.group(2).lower())
    if mon is None:
        return None
    try:
        day = datetime.date(int(held.group(3)), mon, int(held.group(1)))
    except ValueError:
        return None
    t = re.sub(r"\s+", " ", body or "")
    face, isin = _FACE.search(t), _ISIN.search(t)
    if not face or not isin:
        return None
    cpn = _COUPON.search(t)
    remaining = float(rem.group(1))
    frtb = bool(_FRTB.search(title))
    return {"auction_date": day, "face_crore": float(face.group(1).replace(",", "")), "isin": isin.group(1),
            "coupon_pct": float(cpn.group(1)) if cpn else None, "remaining_years": remaining,
            "tenor_label": tenor_class(remaining, frtb)}
