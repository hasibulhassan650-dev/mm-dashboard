"""
monetary.py — real Monetary & Prices indicators from Bangladesh Bank econdata.

Replaces the fabricated api/seeds/monetary.yaml placeholders with REAL published
figures. Three authoritative BB pages:
  - inflation      → CPI point-to-point + 12-month average (latest months)
  - intrate        → scheduled-bank weighted-average deposit & lending rates
                     (full monthly history)
  - monetarysurvey → broad money (M2) and private-sector credit YoY growth
                     (latest month)

Accuracy rule for this project: a real figure or NO figure — never a guess.
A field with no reliable source (e.g. reserve-money growth, CRR/SLR) is simply
OMITTED, so the chart line is blank rather than fabricated.

Run `python -m fetchers.monetary --write` to regenerate api/seeds/monetary.yaml.
"""
import argparse
import datetime
import logging
import re
from typing import Optional

from curl_cffi import requests as creq
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_BASE = "https://www.bb.org.bd/en/index.php/econdata/"
_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _soup(slug: str) -> BeautifulSoup:
    r = creq.get(_BASE + slug, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def _nums(s: str) -> list:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", s)]


def _month_key(text: str) -> Optional[str]:
    """'Jul, 2026' / 'July 2026' → '2026-07'."""
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?,?\s*((?:19|20)\d{2})", text)
    if not m:
        return None
    mo = _MON.get(m.group(1).lower())
    return f"{m.group(2)}-{mo:02d}" if mo else None


# ── individual page parsers ───────────────────────────────────────────────────

def parse_inflation() -> dict:
    """{ '2026-07': {'cpi_p2p': 8.32, 'cpi_12mo_avg': 8.66}, ... } (latest months)."""
    t = _soup("inflation").find("table")
    rows = t.find_all("tr")
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]
    # cell[0] is the title ("Rate of Inflation … from Apr,2023 base …") — it
    # contains a stray date that must NOT be read as a data column. Only the
    # cells after it are the period columns.
    months = [_month_key(h) for h in header[1:]]
    out: dict = {}
    for tr in rows[1:]:
        label = tr.get_text(" ", strip=True).lower()
        vals = _nums(tr.get_text(" ", strip=True))
        if "point to point" in label:
            field = "cpi_p2p"
        elif "average" in label or "twelve" in label:
            field = "cpi_12mo_avg"
        else:
            continue
        # values align to the date columns (skip the label's own leading cells)
        vi = 0
        for mk in months:
            if mk is None:
                continue
            if vi < len(vals) and 0 < vals[vi] < 60:
                out.setdefault(mk, {})[field] = vals[vi]
            vi += 1
    return out


def parse_rates() -> dict:
    """{ '2026-06': {'wavg_deposit': 6.16, 'wavg_lending': 11.86}, ... } full history.
    Columns: period | bankrate | call(borrow) | call(lend) | dep | adv | spread.
    Only rows with all 6 figures AND lending-deposit≈spread are kept."""
    t = _soup("intrate").find("table")
    year: Optional[int] = None
    out: dict = {}
    for tr in t.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue
        head = cells[0].strip()
        if re.fullmatch(r"(19|20)\d{2}", head):
            year = int(head)
            continue
        mo = _MON.get(head.lower()[:3])
        if not mo or year is None:
            continue
        vals = _nums(" ".join(cells[1:]))          # numbers after the month label
        if len(vals) < 6:
            continue                                 # incomplete row (e.g. not-yet-published)
        deposit, lending, spread = vals[3], vals[4], vals[5]
        if deposit <= 0 or lending <= 0:
            continue
        if abs((lending - deposit) - spread) > 0.15:
            continue                                 # column drift guard
        out[f"{year}-{mo:02d}"] = {"wavg_deposit": round(deposit, 2),
                                   "wavg_lending": round(lending, 2)}
    return out


def parse_survey() -> dict:
    """{ '2026-06': {'m2_growth': 11.11, 'private_credit_growth': 4.7} } latest month.
    Private-sector credit growth is computed from levels (BB + DMB claims) for
    accuracy, not by averaging the two published percentages."""
    t = _soup("monetarysurvey").find("table")
    rows = t.find_all("tr")
    # latest data month is the first dated column in the header block
    month = None
    for tr in rows[:4]:
        month = _month_key(tr.get_text(" ", strip=True))
        if month:
            break
    m2 = None
    priv_now = priv_prev = 0.0
    for tr in rows:
        label = tr.get_text(" ", strip=True)
        low = label.lower()
        vals = _nums(label)
        if "broad money" in low and vals:
            m2 = vals[-1]                                     # YoY % is the last column
        elif "claims on private sector" in low and len(vals) >= 3:
            priv_now += vals[0]                               # level, latest month
            priv_prev += vals[2]                             # level, year ago
    out: dict = {}
    if month:
        rec = {}
        if m2 is not None:
            rec["m2_growth"] = round(m2, 2)
        if priv_prev > 0:
            rec["private_credit_growth"] = round((priv_now / priv_prev - 1) * 100, 2)
        if rec:
            out[month] = rec
    return out


# ── assembly ──────────────────────────────────────────────────────────────────

def build_monetary() -> dict:
    """Merge the three sources into { 'monthly': [ {month, ...fields}, ... ] }.
    Only real, sourced fields are included; anything unavailable is omitted."""
    by_month: dict = {}
    for src in (parse_rates(), parse_inflation(), parse_survey()):
        for mk, rec in src.items():
            by_month.setdefault(mk, {}).update(rec)
    monthly = [{"month": mk, **{k: v for k, v in sorted(rec.items())}}
               for mk, rec in sorted(by_month.items())]
    return {"monthly": monthly,
            "_source": "Bangladesh Bank econdata (inflation, intrate, monetarysurvey)",
            "_fetched_utc": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}


def write_yaml(path: str) -> int:
    import yaml
    data = build_monetary()
    # CRR/SLR is NOT carried forward: the previous values were marked DRAFT and
    # are not sourced by these pages. Better an honest "—" than an unverified
    # number. Wire CRR/SLR from a BB circular/monetary-policy source separately.
    header = (
        "# Bangladesh - Monetary & Prices indicators\n"
        "# AUTO-GENERATED from Bangladesh Bank econdata by fetchers/monetary.py.\n"
        f"# Source: {data['_source']}\n"
        f"# Fetched: {data['_fetched_utc']}\n"
        "# Only real, sourced fields are present; unavailable ones (reserve-money\n"
        "# growth, CRR/SLR) are omitted rather than fabricated.\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump({"monthly": data["monthly"]}, f, sort_keys=False, allow_unicode=True)
    return len(data["monthly"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", metavar="PATH", nargs="?",
                    const="api/seeds/monetary.yaml", default=None,
                    help="regenerate the monetary seed YAML with real data")
    args = ap.parse_args()
    if args.write:
        n = write_yaml(args.write)
        log.info("Wrote %d real monthly rows to %s", n, args.write)
    else:
        import json
        print(json.dumps(build_monetary(), indent=2, default=str))
