"""Reading Bangla news safely: the amount and the meaning of a Bangla sentence are checked by CODE, as in English.

A language model may READ a Bangla article, but what it reports is never trusted as given (engines/liquidity_news.py):
the quote must be in the article, the amount is re-read from the quote, a request is not a payment, a running total
is not new cash. Those guards were English-only, so Bangla sources stayed switched off until this module existed.

Everything here is a pure function of text. When the code cannot tell, it says so (None / False) rather than guessing:
a figure that is not certain is left blank, never stored.
"""
import re
import unicodedata
from typing import List, Optional

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_INVISIBLE = dict.fromkeys(map(ord, "‌‍﻿​"), None)       # ZWNJ, ZWJ, BOM, ZWSP
_BANGLA = re.compile(r"[ঀ-৿]")


def is_bangla(text: str, min_share: float = 0.2) -> bool:
    """True when a meaningful share of the letters are Bangla (an English article quoting one word is not)."""
    t = text or ""
    letters = sum(1 for c in t if c.isalpha() or _BANGLA.match(c))
    return bool(letters) and len(_BANGLA.findall(t)) / letters >= min_share


def normalize(text: str) -> str:
    """NFC, no zero-width joiners (Bangla titles carry them and they break a verbatim-quote match), Bangla digits as
    ASCII digits."""
    return unicodedata.normalize("NFC", text or "").translate(_INVISIBLE).translate(_BN_DIGITS)


# crore taka per unit. A unit not on this list is not read: "বিলিয়ন ডলার" is dollars, never taka.
_UNIT = {"লাখ কোটি": 100000.0, "হাজার কোটি": 1000.0, "কোটি": 1.0, "বিলিয়ন": 100.0, "মিলিয়ন": 0.1,
         "লাখ": 0.01, "হাজার": 0.0001}
_UNIT_RX = r"(?P<unit>লাখ\s*কোটি|হাজার\s*কোটি|কোটি|বিলিয়ন|মিলিয়ন|লাখ|হাজার)"
_NUM_RX = r"\d[\d,]*(?:\.\d+)?"
# fraction words that stand before a unit: দেড় কোটি (1.5), আড়াই কোটি (2.5); সাড়ে ৩ (3.5), সোয়া ৩ (3.25), পৌনে ৩ (2.75)
_FIXED = {"দেড়": 1.5, "আড়াই": 2.5}
_OFFSET = {"সাড়ে": 0.5, "সোয়া": 0.25, "পৌনে": -0.25}
_AMOUNT = re.compile(
    rf"(?:(?P<fixed>{'|'.join(_FIXED)})|(?P<off>{'|'.join(_OFFSET)})\s*(?P<n_off>{_NUM_RX})|(?P<n>{_NUM_RX}))\s*{_UNIT_RX}\s*"
    rf"(?P<cur>টাকা|ডলার|মার্কিন\s*ডলার|ইউরো|রুপি)?")


def amounts_crore(text: str) -> List[float]:
    """Every TAKA amount in a Bangla (or mixed) text, in crore. Dollars, euros and rupees are not taka and are skipped;
    an amount with no currency word is skipped (a bare "৫০০ কোটি" may be anything); a compound like "৩ হাজার ৫০০ কোটি
    টাকা" is skipped, because reading only its tail as 500 would store a figure 7x too small."""
    t = normalize(text)
    out: List[float] = []
    for m in _AMOUNT.finditer(t):
        if m.group("cur") != "টাকা":
            continue
        before = t[max(0, m.start() - 14):m.start()]
        if re.search(rf"{_NUM_RX}\s*(?:হাজার|লাখ)\s*$", before):
            continue                                            # tail of a compound number: ambiguous, not read
        unit = re.sub(r"\s+", " ", m.group("unit"))
        try:
            if m.group("fixed"):
                n = _FIXED[m.group("fixed")]
            elif m.group("off"):
                n = float(m.group("n_off").replace(",", "")) + _OFFSET[m.group("off")]
            else:
                n = float(m.group("n").replace(",", ""))
        except ValueError:
            continue
        out.append(round(n * _UNIT[unit], 4))
    return out


# ── meaning ───────────────────────────────────────────────────────────────────
# a request, demand, estimate or plan is not an event
_REQUEST = re.compile(r"চেয়েছে|চেয়েছেন|চাওয়া|চেয়ে\s*আবেদন|আবেদন|প্রস্তাব|পরিকল্পনা|প্রত্যাশা|আশা\s*করা|সম্ভাবনা|"
                      r"লক্ষ্যমাত্রা|প্রাক্কলন|পূর্বাভাস|দাবি|হতে\s*পারে|করতে\s*পারে|চায়|চান")
# a future-tense announcement: no cash has moved
_FUTURE = re.compile(r"করবে|দেবে|হবে|ছাড়\s*করা\s*হবে|ছাড়\s*হবে|দিতে\s*যাচ্ছে|দেওয়া\s*হবে|দেয়া\s*হবে")
# an act that has happened
_DONE = re.compile(r"ছাড়\s*করেছে|ছাড়\s*করা\s*হয়েছে|ছাড়\s*হয়েছে|বিতরণ\s*করেছে|বিতরণ\s*করা\s*হয়েছে|পরিশোধ\s*করেছে|"
                   r"পরিশোধ\s*করা\s*হয়েছে|দিয়েছে|দেওয়া\s*হয়েছে|দেয়া\s*হয়েছে|ঋণ\s*নিয়েছে|কমিয়েছে|বাড়িয়েছে|"
                   r"কমানো\s*হয়েছে|বাড়ানো\s*হয়েছে|কিনেছে|বিক্রি\s*করেছে|দিয়েছে|জারি\s*করেছে")
# a running total, not new cash
_STOCK = re.compile(r"এ\s*পর্যন্ত|এখন\s*পর্যন্ত|আজ\s*পর্যন্ত|স্থিতি|সঞ্চিত|বকেয়া\s*স্থিতি|সংখ্যা\s*দাঁড়িয়েছে")
# a total over a period: kept, marked cumulative, never summed
_PERIOD = re.compile(r"মোট|জুলাই[-\s]*আগস্ট|প্রথম\s*(?:ত্রৈমাসিক|প্রান্তিক|তিন\s*মাস)|অর্থবছরে|পর্যন্ত|ত্রৈমাসিকে")


def tense(sentence: str):
    """(done, future) for a Bangla sentence. Done wins when a completed verb is present and no future marker precedes it."""
    s = normalize(sentence)
    d, f = _DONE.search(s), _FUTURE.search(s)
    done = bool(d) and not (f is not None and f.start() < d.start())
    return done, f is not None and not done


def is_request(sentence: str) -> bool:
    return bool(_REQUEST.search(normalize(sentence)))


def is_stock(sentence: str) -> bool:
    return bool(_STOCK.search(normalize(sentence)))


def is_period_total(sentence: str) -> bool:
    return bool(_PERIOD.search(normalize(sentence)))


# A scheme's SIZE, not cash that moved: the amount is directly followed by the scheme noun
# ("১০ হাজার কোটি টাকার পুনঃঅর্থায়ন স্কিম" = "a Tk 10,000 crore refinance scheme"). Anything else is an act (the default).
_SCHEME_WORDS = r"(?:স্কিম|তহবিল|প্যাকেজ|সুবিধা|ঋণসুবিধা|প্রকল্প)"
_FACILITY = re.compile(rf"টাকা(?:র|য়)?\s+(?:[^\s,।]+\s+){{0,2}}?{_SCHEME_WORDS}")
_ENROLMENT = re.compile(r"অংশগ্রহণ(?:মূলক)?\s*চুক্তি|চুক্তি\s*স্বাক্ষর")


def is_facility(sentence: str) -> bool:
    return bool(_FACILITY.search(normalize(sentence)))


def is_enrolment(sentence: str) -> bool:
    """One bank signing up to a scheme ('অংশগ্রহণ চুক্তি'): an allocation, not cash. False when the code cannot tell."""
    s = normalize(sentence)
    return bool(_ENROLMENT.search(s) and re.search(_SCHEME_WORDS, s))


# ── the keyword prefilter's Bangla words (cheap, explicit, auditable) ─────────
ACTORS = ("বাংলাদেশ ব্যাংক", "কেন্দ্রীয় ব্যাংক", "অর্থ বিভাগ", "অর্থ মন্ত্রণালয়", "অর্থমন্ত্রণালয়", "অর্থ উপদেষ্টা",
          "পেট্রোবাংলা", "বিপিডিবি", "বিপিসি", "আইএমএফ", "বিশ্বব্যাংক", "এডিবি", "জ্বালানি বিভাগ", "বিদ্যুৎ বিভাগ")
ACTIONS = ("ভর্তুকি", "তারল্য", "ছাড়", "বিতরণ", "রেপো", "সিআরআর", "এসএলআর", "নগদ জমা", "পুনঃঅর্থায়ন", "প্রাক-অর্থায়ন",
           "সঞ্চয়পত্র", "ডলার বিক্রি", "ডলার কিনে", "বকেয়া", "ঋণ", "টাকা ছাপ", "ওভারড্রাফট", "বিল")
MONEY = ("টাকা", "কোটি", "লাখ", "ডলার", "বিলিয়ন")
