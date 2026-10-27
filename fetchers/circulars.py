"""
fetchers/circulars.py - BB circulars that close (or reopen) the banks.

The gazetted holiday list does not carry everything. Sharadiya Durga Puja 2026 closed the banks
on 20-22 Oct by SDAD circular letter 15 of 11-Oct; the gazette had 20 and 21. A closure the
calendar does not know leaves every maturity, coupon and settlement of that day on a day the market
was shut - the OMO ledger then reports a missing injection that never went missing.

BB lists circulars on /mediaroom/circular as rows "dd/mm/yy | <DEPT> Circular Letter No. N: <title> |
... | Download". The department that tells scheduled banks when to close is SDAD (Supervisor of Data
Management and Analytics); the same title from FCRPD/DFIM is for finance companies, which are not
the money market, and is left alone. The circular text is in Bengali and the PDFs use a legacy font,
so the digits come through but many letters do not: that is handled in engines/calendar_notices.py,
which would rather leave a notice PENDING than guess.
"""
import datetime
import html as html_mod
import io
import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

CIRCULAR_URL = "https://www.bb.org.bd/en/index.php/mediaroom/circular"
BASE = "https://www.bb.org.bd"

# Titles about closing/opening. "Pre-Finance Scheme to support the closed industry" is a loan scheme
# and "Keeping Bank Branches Operational on Saturdays During Mango Season" is a few branches, so the
# words have to describe the BANKS being closed or open - and the department has to be SDAD.
CLOSURE_TITLE = re.compile(r"closure|closed|remain(?:s)? open|open/closed|general holiday|bank holiday|holiday",
                           re.I)
NOT_THE_MARKET = re.compile(r"industry|branch|ATM|wage|salary|allowance|bonus|scheme|mango", re.I)


def department_of(url: str) -> str:
    m = re.search(r"/circulars/([^/]+)/", url or "")
    return m.group(1).lower() if m else ""


def parse_listing(html: str) -> List[Dict]:
    """Every SDAD circular whose title is about banks closing or opening, newest first."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", re.S):
        cells = [html_mod.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        line = " | ".join(re.sub(r"\s+", " ", c) for c in cells if c)
        d = re.match(r"(\d{2})/(\d{2})/(\d{2})\b", line)
        pdfs = re.findall(r'href="([^"]+\.pdf)"', row, re.I)
        if not (d and pdfs):
            continue
        url = pdfs[0] if pdfs[0].startswith("http") else BASE + pdfs[0]
        title = line.split("|")[1].strip() if "|" in line else ""
        if department_of(url) != "sdad" or not CLOSURE_TITLE.search(title) or NOT_THE_MARKET.search(title):
            continue
        try:
            published = datetime.date(2000 + int(d.group(3)), int(d.group(2)), int(d.group(1)))
        except ValueError:
            continue
        out.append({"published_date": published, "title": title, "url": url,
                    "department": department_of(url)})
    out.sort(key=lambda r: r["published_date"], reverse=True)
    return out


def fetch_listing() -> Dict:
    """{"rows": [...], "transport_error": None | str}.

    Failing to read the page is never evidence about the calendar: a maintenance page, a CAPTCHA
    or an empty table is reported as a transport error and nothing is parsed from it."""
    from curl_cffi import requests as cr

    from fetchers.bb_health import check_response, looks_blocked_page
    try:
        r = cr.get(CIRCULAR_URL, impersonate="chrome", timeout=90)
        check_response(r)
    except Exception as exc:
        return {"rows": [], "transport_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if looks_blocked_page(r.text) or "<table" not in r.text.lower():
        return {"rows": [], "transport_error": "the circular page was not served (challenge or no table)"}
    rows = parse_listing(r.text)
    return {"rows": rows, "transport_error": None}


def read_pdf_text(url: str) -> Optional[str]:
    """The PDF's text, or None when it could not be fetched as a PDF."""
    from curl_cffi import requests as cr
    try:
        r = cr.get(url, impersonate="chrome", timeout=60)
    except Exception as exc:
        log.warning("circular PDF %s: %s", url, type(exc).__name__)
        return None
    if r.status_code != 200 or r.content[:4] != b"%PDF":
        return None
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as exc:
        log.warning("circular PDF %s unreadable: %s", url, exc)
        return None
