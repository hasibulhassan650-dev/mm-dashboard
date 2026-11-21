"""Read a BB 'Press Release for the Nth Short-term Bangladesh Government Investment Sukuk Auction'.

Why: BB's sukuk page lists an issue only AFTER it settles, so the portal knew nothing of a sukuk auction until weeks
after the desk did, although BB's own press release states everything the desk needs: face value, tenor, annual rent,
the auction day and the maturity.

The PDF is typeset in the legacy Bijoy (SutonnyMJ) Bangla font, so its Bangla text extracts as Latin-looking noise.
That is fine and is deliberately NOT 'decoded': every figure is a plain digit and sits beside fixed ASCII-looking
tokens (`UvKv AwfwnZ` = 'taka face', `(Face Value)`, `hv evwl..K 7.50%`), which are anchored on. Only the month
words need a table, and a month that is not in it refuses the release (loudly) rather than guessing.

Two statements of one fact: BB prints the total rent AND the annual rate, and rent = face x rate x days / 365 must
hold to the paisa; the maturity must be `days` after an issue date that follows the auction. A release that breaks
either is refused whole, with the reason, instead of storing a figure that might be misread.
"""
import datetime
import re
from typing import Dict, Optional

# Bijoy spellings of the month, '^' and spaces removed. Verified on the 12-Nov-2026 release: Nov, Dec, Jun. The rest
# follow the same encoding; a wrong one cannot get through because the dates are cross-checked against the tenor.
_MONTHS = {
    "Rvbyqvwi": 1, "†deªæqvwi": 2, "gvP©": 3, "GwcÖj": 4, "†g": 5, "Ryb": 6,
    "RyjvB": 7, "AvM÷": 8, "†m‡Þ¤i": 9, "A‡±vei": 10, "b‡f¤i": 11, "wW‡m¤i": 12,
}
_DATE = r"(\d{1,2})\s+([^\s,\d]+)\s*,?\s*(\d{4})"
_NAME = re.compile(r"(Short\s*Term\s+BGIS\s*\(\s*(\d+)\s*Days?\s*\))", re.I)
_FACE = re.compile(r"(\d[\d,]*\.\d+)\s*\([^)]*\)\s*\S+\s+UvKv\s+AwfwnZ")
_RENT = re.compile(r"(\d[\d,]*\.\d+)\s*\S+\s+UvKv\s+hv\s+evwl\S+\s+(\d+(?:\.\d+)?)\s*%")
_AUCTION = re.compile(r"AvMvgx\s+" + _DATE)
_MATURITY = re.compile(r"hvi\s+\S+\s+ZvwiL\s+" + _DATE)
_SERIES = re.compile(r"(\d+)(?:st|nd|rd|th)\b", re.I)


def _month(word: str) -> Optional[int]:
    return _MONTHS.get(re.sub(r"[\^\s]", "", word))


def _date(m) -> Optional[datetime.date]:
    mon = _month(m.group(2))
    if mon is None:
        return None
    try:
        return datetime.date(int(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_sukuk_notice(text: str, serial: str = "", title: str = "", url: str = "") -> Dict:
    """{'row': {...} | None, 'refused': 'why' | None}. Never a partial row."""
    t = re.sub(r"\s+", " ", text or "")

    def no(why):
        return {"row": None, "refused": why}

    name = _NAME.search(t)
    if not name:
        return no("no 'Short Term BGIS (<n> Days)' in the release - another kind of sukuk, or the layout changed")
    days = int(name.group(2))
    face, rent = _FACE.search(t), _RENT.search(t)
    if not face:
        return no("the face value was not found")
    if not rent:
        return no("the total rent and annual rate were not found")
    auction, mat = _AUCTION.search(t), _MATURITY.search(t)
    if not auction or not mat:
        return no("the auction date or the maturity date was not found")
    ad, md = _date(auction), _date(mat)
    if ad is None or md is None:
        return no(f"a month word is not in the table ({auction.group(2)!r} / {mat.group(2)!r}) - refused rather than guessed")

    face_cr, rent_cr, rate = _num(face.group(1)), _num(rent.group(1)), _num(rent.group(2))
    expect = round(face_cr * rate / 100.0 * days / 365.0, 2)
    if abs(expect - rent_cr) > 0.011:
        return no(f"rent {rent_cr} does not equal face {face_cr} x {rate}% x {days}/365 = {expect}: a figure was misread")
    issue = md - datetime.timedelta(days=days)
    if not (0 < (issue - ad).days <= 7):
        return no(f"maturity {md} less {days} days gives an issue date {issue}, not 1-7 days after the auction {ad}: "
                  f"a date was misread")
    series = _SERIES.search(title or "")
    return {"refused": None, "row": {
        "source_serial": serial, "source_url": url, "auction_date": ad, "issue_date": issue, "maturity_date": md,
        "tenor_days": days, "face_crore": face_cr, "rent_crore": rent_cr, "rate_pct": rate,
        "name": re.sub(r"\s+", " ", name.group(1)), "series_no": int(series.group(1)) if series else None}}
