"""
fetchers/policy_rates.py — Bangladesh Bank policy-rate corridor, live from
BB's homepage POLICY RATES box (the authoritative source). Runs daily in the
pipeline so an MPC rate change is reflected within a day and never depends on
someone hand-editing a seed again.

Structure on bb.org.bd (stable div-grid):
  <div class="policy"> ... <div class="display_table">
     <div><div>Policy Rate (Repo Rate)</div><div>9.50%</div></div>
     <div><div>SLF Rate</div><div>11.00%</div></div>
     <div><div>SDF Rate</div><div>7.50%</div></div>
     <div><div>Bank Rate</div><div>4.00%</div></div>
  </div> ... <p class="last_update">Last update: 15.02.2026</p>
Also reads the adjacent RESERVE RATIOS box (CRR/SLR) when present.

NEVER detect blocking by 'TSPD'/'bobcmn' strings (they appear on the real
page too) — always use a positive content check ("POLICY RATES").
"""
import logging
import re
from typing import Optional, Dict

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/home"


def _pct(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", re.sub(r"<[^>]+>", "", s))
    return float(m.group(1)) if m else None


def _fetch_html() -> Optional[str]:
    """curl_cffi with Chrome TLS impersonation is enough for the homepage;
    fall back to the Chrome-F5 bypass if BB ever walls it."""
    try:
        from curl_cffi import requests as cr
        r = cr.get(_URL, impersonate="chrome", timeout=30)
        if r.status_code == 200 and "POLICY RATES" in r.text:
            log.info("policy: fetched BB homepage (%d bytes)", len(r.text))
            return r.text
        log.warning("policy: homepage lacked POLICY RATES (status=%s)", r.status_code)
    except Exception as exc:
        log.warning("policy: curl_cffi fetch failed: %s", exc)
    try:
        from fetchers.bb_session import get_f5_cookies, bb_get
        cookies, ua = get_f5_cookies(_URL, wait_selector=".policy")
        html = bb_get(_URL, cookies, ua)
        if html and "POLICY RATES" in html:
            log.info("policy: fetched BB homepage via F5 bypass (%d bytes)", len(html))
            return html
    except Exception as exc:
        log.error("policy: F5 fallback failed: %s", exc)
    return None


def _pairs_in_section(html: str, start_marker: str, end_marker: str) -> Dict[str, str]:
    """Pull <div><div>LABEL</div><div>VALUE</div></div> pairs from one box."""
    i = html.find(start_marker)
    if i < 0:
        return {}
    j = html.find(end_marker, i)
    box = html[i: j if j > 0 else i + 4000]
    pairs = re.findall(r"<div>\s*<div>(.*?)</div>\s*<div>(.*?)</div>", box, re.S)
    out: Dict[str, str] = {}
    for k, v in pairs:
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", k)).strip().lower()
        if label:
            out[label] = v
    return out


def fetch_policy_rates() -> Optional[Dict]:
    """
    Return the current corridor, or None if the page can't be parsed:
      {repo, slf, sdf, bank_rate, crr, slr, source_last_update, source}
    Requires repo+slf+sdf to succeed — a partial parse returns None (loud).
    """
    html = _fetch_html()
    if not html:
        log.error("policy: could not fetch BB homepage — corridor NOT refreshed")
        return None

    labels = _pairs_in_section(html, "POLICY RATES", "update_readmore")
    if not labels:
        labels = _pairs_in_section(html, 'class="policy"', "update_readmore")

    def find(*keys: str) -> Optional[float]:
        for lab, val in labels.items():
            if all(k in lab for k in keys):
                return _pct(val)
        return None

    repo = find("repo") if find("repo") is not None else find("policy", "rate")
    slf = find("slf")
    sdf = find("sdf")
    bank = find("bank")

    rr = _pairs_in_section(html, "RESERVE RATIOS", "update_readmore")
    def rfind(*keys: str) -> Optional[float]:
        for lab, val in rr.items():
            if all(k in lab for k in keys):
                return _pct(val)
        return None
    crr = rfind("crr") if rfind("crr") is not None else rfind("cash", "reserve")
    slr = rfind("slr") if rfind("slr") is not None else rfind("statutory")

    lu = re.search(r"Last update:\s*([0-9.\-/]+)", html)
    result = {
        "repo": repo, "slf": slf, "sdf": sdf, "bank_rate": bank,
        "crr": crr, "slr": slr,
        "source_last_update": lu.group(1) if lu else None,
        "source": _URL,
    }

    if repo is None or slf is None or sdf is None:
        log.error("policy: incomplete parse (repo/slf/sdf required) -> %s", result)
        return None

    log.info("policy rates: repo=%s SLF=%s SDF=%s bank=%s CRR=%s SLR=%s",
             repo, slf, sdf, bank, crr, slr)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import json
    print(json.dumps(fetch_policy_rates(), indent=2))
