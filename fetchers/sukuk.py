"""BD Govt Investment Sukuk (BGIS): the product this dashboard held none of.

BB's Sukuk portal has three pages we read, all plain HTML over plain HTTP:

  sukukoutstanding/1   a real table - ISIN, project, tenor, contract, issue and
                       maturity dates, profit rate, frequency, tradability and
                       the outstanding amount in crore. Eleven issues today,
                       among them a 273-day short-term BGIS of 5,500 crore that
                       matures on 29-Mar-2027.
  auc_notice_sukuk     auction notices, one row each: BB's reference, a date, a
  prospectus_sukuk     title and a PDF. Prospectuses have the same shape.

Why it matters: a sukuk maturing is government cash going INTO the market, the
same as a bond redemption, and a new notice means a new issue that will pull
cash out. A product missing from a dashboard whose first rule is never to miss
one is the failure this module exists to close.

Columns are mapped by what BB calls them (source_watch.map_columns), never by
position - the same rule as every other BB parser here. BB's headers carry its
own typos ("Underlying Contacts", "Rent al rate"); the matching words are chosen
to survive them.
"""
import datetime
import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger("sukuk")

_BASE = "https://www.bb.org.bd/en/index.php/monetaryactivity/"
OUTSTANDING_URL = _BASE + "sukukoutstanding/1"
DOC_PAGES = (("NOTICE", _BASE + "auc_notice_sukuk"),
             ("PROSPECTUS", _BASE + "prospectus_sukuk"))


def _num(s: str) -> Optional[float]:
    s = re.sub(r"[^\d.\-]", "", s or "")
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _date(s: str) -> Optional[datetime.date]:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_outstanding(html: str) -> dict:
    """The BGIS Outstanding table -> rows, refusals, a structural fingerprint."""
    from bs4 import BeautifulSoup

    from fetchers import source_watch as SW

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return {"rows": [], "refused": {}, "meta": SW.fingerprint(
            "sukuk_outstanding", OUTSTANDING_URL, error="the page carries no table")}
    table = max(tables, key=lambda t: len(t.find_all("tr")))
    trs = [tr for tr in table.find_all("tr") if tr.find_all(["th", "td"])]
    header_rows = [tr for tr in trs if tr.find("th")]
    data = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            for tr in trs if not tr.find("th")]

    headers = SW.header_columns(header_rows)
    spec = (
        SW.Column("isin", ["isin"], required=True),
        SW.Column("project", ["sukuk", "project"], required=True),
        SW.Column("tenor", ["tenor"]),
        SW.Column("contract", ["underlying"]),
        SW.Column("issue_date", ["issuing", "date"], required=True),
        SW.Column("maturity_date", ["maturity", "date"], required=True),
        SW.Column("rate", ["profit", "rate"], none_of=["frequency"]),
        SW.Column("frequency", ["frequency"]),
        SW.Column("tradability", ["tradability"]),
        SW.Column("outstanding", ["outstanding"], required=True),
    )
    idx, notes = SW.map_columns(headers, spec, "sukuk_outstanding")   # raises if BB moves

    rows: List[Dict] = []
    refused: Dict[str, int] = {}

    def refuse(why):
        refused[why] = refused.get(why, 0) + 1

    def cell(r, f):
        i = idx.get(f)
        return r[i].strip() if i is not None and i < len(r) else ""

    for r in data:
        isin = cell(r, "isin").upper()
        if not re.fullmatch(r"[A-Z0-9]{10,14}", isin):
            refuse("no ISIN")
            continue
        issue, mat = _date(cell(r, "issue_date")), _date(cell(r, "maturity_date"))
        out = _num(cell(r, "outstanding"))
        if issue is None or mat is None:
            refuse(f"{isin}: unreadable date")
            continue
        if mat <= issue:
            refuse(f"{isin}: maturity not after issue")
            continue
        if out is None or out < 0:
            refuse(f"{isin}: unreadable outstanding amount")
            continue
        rows.append({
            "isin": isin, "project": cell(r, "project"), "tenor_label": cell(r, "tenor") or None,
            "contract": cell(r, "contract") or None, "issue_date": issue, "maturity_date": mat,
            "profit_rate_pct": _num(cell(r, "rate")), "profit_frequency": cell(r, "frequency") or None,
            "tradability": cell(r, "tradability") or None, "outstanding_crore": out,
        })
    meta = SW.fingerprint("sukuk_outstanding", OUTSTANDING_URL, selector="largest table",
                          headers=headers, rows_seen=len(data), rows_parsed=len(rows),
                          skips=refused, unmapped=notes["unmapped"], mapped=idx)
    return {"rows": rows, "refused": refused, "meta": meta}


def parse_documents(html: str, kind: str) -> dict:
    """Notice / prospectus rows: BB reference, dd/mm/yyyy, title, PDF."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    rows, refused = [], {}
    for div in soup.select("div.row-data"):
        a = div.find("a", attrs={"pdf-link": True})
        no = div.select_one(".auction_no")
        title = div.select_one(".auction_title")
        if a is None or no is None or title is None:
            refused["row without link/reference/title"] = refused.get("row without link/reference/title", 0) + 1
            continue
        parts = [p.strip() for p in no.get_text("\n").split("\n") if p.strip()]
        d = _date(parts[-1]) if parts else None
        url = a["pdf-link"].strip()
        if d is None or not url.lower().endswith(".pdf"):
            refused["unreadable date or not a PDF"] = refused.get("unreadable date or not a PDF", 0) + 1
            continue
        stem = re.sub(r"\W+", "", url.rsplit("/", 1)[-1].rsplit(".", 1)[0]).lower()
        rows.append({"url": url, "kind": kind, "doc_date": d,
                     "ref": parts[0] if len(parts) > 1 else None,
                     "title": re.sub(r"\s+", " ", title.get_text(" ", strip=True)),
                     "doc_key": f"sukuk-{stem}"})
    return {"rows": rows, "refused": refused}


def fetch_sukuk() -> dict:
    """Read all three pages. No browser; a page that cannot be read says why."""
    from curl_cffi import requests as cr

    from fetchers import source_watch as SW
    from fetchers.bb_health import check_response

    out = {"outstanding": [], "documents": [], "refused": {}, "errors": [], "meta": None}

    def get(url):
        r = cr.get(url, impersonate="chrome", timeout=30)
        check_response(r)           # BB's maintenance page is named as such
        return r.text or ""

    def looks_blocked(html: str) -> bool:
        """A real BB page is tens of kilobytes. A 200 with no table and a few kilobytes of
        body is a challenge or block page served to this address - a fact about the
        transport, not BB reorganising its site (which is what 'no table' would record)."""
        return "<table" not in html.lower() and len(html) < 15000

    try:
        page = get(OUTSTANDING_URL)
        if looks_blocked(page):
            out["errors"].append(f"sukuk outstanding: page not served ({len(page)} bytes, no table)")
            out["meta"] = SW.fingerprint("sukuk_outstanding", OUTSTANDING_URL,
                                         transport_error=f"{len(page)}-byte page without a table")
            page = None
        o = parse_outstanding(page) if page else {"rows": [], "refused": {}, "meta": out["meta"]}
        out["outstanding"], out["meta"] = o["rows"], o["meta"]
        out["refused"].update({f"outstanding: {k}": v for k, v in o["refused"].items()})
    except SW.StructureChanged as exc:
        out["errors"].append(f"sukuk outstanding: {exc}")
        out["meta"] = SW.fingerprint("sukuk_outstanding", OUTSTANDING_URL, error=str(exc))
    except Exception as exc:
        out["errors"].append(f"sukuk outstanding: {type(exc).__name__} {exc}")
        out["meta"] = SW.fingerprint("sukuk_outstanding", OUTSTANDING_URL,
                                     transport_error=f"{type(exc).__name__} {exc}")

    for kind, url in DOC_PAGES:
        try:
            d = parse_documents(get(url), kind)
            out["documents"].extend(d["rows"])
            out["refused"].update({f"{kind.lower()}: {k}": v for k, v in d["refused"].items()})
        except Exception as exc:
            out["errors"].append(f"sukuk {kind.lower()}s: {type(exc).__name__} {exc}")
    return out
