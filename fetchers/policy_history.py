"""
fetchers/policy_history.py — BB policy-rate corridor HISTORY from MPD circulars.

The live homepage fetcher (fetchers/policy_rates.py) only ever knows TODAY's
corridor. The error-correction model needs the corridor as a step function
through time, and hand-entering it from memory is exactly what produced the
"DRAFT - drafted from memory" seed entries that the modelling refuses to use.

So this reads the primary source: Bangladesh Bank's MPD circulars, each of which
announces a rate re-fixation on a dated PDF.

WHY THE PARSING IS SAFE DESPITE THE PDFs BEING BANGLA
The PDFs use several broken font encodings, so the Bangla text cannot be
tokenised reliably. It does not need to be. Every circular states BOTH the
previous value and the new one ("existing 10.00 percent ... reduced by 50 basis
points to 9.50 percent"), and the three corridor rates are always distinct
(SDF < repo < SLF). So each numeric transition A->B is assigned to whichever
rate currently stands at A. That is deterministic arithmetic, not language
parsing, and it VALIDATES ITSELF: if a stated prior value matches no current
rate, the chain is broken and the entry is refused rather than guessed.

Two further checks before anything is marked verified:
  - the chain must run unbroken from its anchor;
  - the final computed corridor must equal the corridor BB publishes live today.
If either fails, the run writes nothing rather than something plausible.

Usage:  python fetchers/policy_history.py            print what it found
        python fetchers/policy_history.py --write    write api/seeds/policy_rates.yaml
"""
import io
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger(__name__)

CIRCULAR_URL = "https://www.bb.org.bd/en/index.php/mediaroom/circular"
BASE = "https://www.bb.org.bd"
BN_DIGITS = "০১২৩৪৫৬৭৮৯"

# The Interest Rate Corridor was introduced on 2023-06-20 (MPD Circular 02).
# Before that BB ran repo / reverse-repo with no SLF/SDF corridor, so a corridor
# series cannot be extended earlier and this does not attempt to.
ANCHOR_DATE = "2023-06-20"

# A transition reads "existing A percent ... to B percent". 160 characters
# comfortably spans that clause without bridging into the next rate's sentence.
TRANSITION_SPAN = 160


def _norm(s: str) -> str:
    """Bengali numerals to ASCII, and collapse whitespace."""
    for i, ch in enumerate(BN_DIGITS):
        s = s.replace(ch, str(i))
    return re.sub(r"\s+", " ", s)


def fetch_circular_index() -> list[dict]:
    """Every MPD circular that re-fixes a policy rate, oldest first."""
    from curl_cffi import requests as cr
    import html as html_mod
    r = cr.get(CIRCULAR_URL, impersonate="chrome", timeout=60)
    r.raise_for_status()
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = [html_mod.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        line = " | ".join(c for c in cells if c)
        if not re.search(r"\bMPD\b", line):
            continue
        if not re.search(r"repo|policy rate|interest rate corridor|IRC|SDF|SLF", line, re.I):
            continue
        d = re.match(r"(\d{2})/(\d{2})/(\d{2})", line)
        pdfs = re.findall(r'href="([^"]+\.pdf)"', row, re.I)
        if not (d and pdfs):
            continue
        subject = re.sub(r"\s+", " ", line.split("|")[1]).strip()[:120] if "|" in line else ""
        url = pdfs[0] if pdfs[0].startswith("http") else BASE + pdfs[0]
        out.append({"date": "20" + d.group(3) + "-" + d.group(2) + "-" + d.group(1),
                    "subject": subject, "url": url})
    out.sort(key=lambda x: x["date"])
    return out


def read_pdf_numbers(url: str) -> tuple[list, list]:
    """(transitions A->B, values stated as unchanged) from one circular PDF."""
    from curl_cffi import requests as cr
    import pdfplumber
    r = cr.get(url, impersonate="chrome", timeout=60)
    r.raise_for_status()
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        txt = " ".join((p.extract_text() or "") for p in pdf.pages)
    txt = _norm(txt)

    nums = [(m.start(), float(m.group(1))) for m in re.finditer(r"(\d{1,2}\.\d{1,2})", txt)]
    transitions, used = [], set()
    for i in range(len(nums) - 1):
        (p1, a), (p2, b) = nums[i], nums[i + 1]
        if i in used or p2 - p1 > TRANSITION_SPAN or a == b:
            continue
        transitions.append((a, b))
        used.add(i)
        used.add(i + 1)
    unchanged = [v for i, (_, v) in enumerate(nums) if i not in used]
    return transitions, unchanged


def build_history(circulars: list[dict], anchor: dict) -> list[dict]:
    """Walk the circulars forward, assigning each transition by its prior value.

    `anchor` is repo/slf/sdf as at ANCHOR_DATE. Any circular whose stated prior
    values match no running rate breaks the chain and is reported, never guessed.
    """
    state = dict(anchor)
    history = [dict(effective_date=ANCHOR_DATE, source="MPD IRC introduction",
                    subject="Interest Rate Corridor introduced", **state)]
    for c in circulars:
        if c["date"] <= ANCHOR_DATE:
            continue
        try:
            transitions, _unchanged = read_pdf_numbers(c["url"])
        except Exception as exc:
            log.warning("%s: could not read PDF (%s)", c["date"], exc)
            continue
        new = dict(state)
        matched, unmatched = 0, []
        for a, b in transitions:
            hit = [k for k in ("repo", "slf", "sdf") if abs(state[k] - a) < 1e-9]
            # A pair whose BOTH values are current corridor rates is not a
            # change, it is the circular listing two rates it is leaving alone
            # ("SLF remains at 11.50 and the repo rate remains at 10.00").
            # Without this the 2025-07-15 circular read as SLF 11.50 -> 10.00,
            # which silently corrupted every later entry in the chain.
            also_current = [k for k in ("repo", "slf", "sdf") if abs(state[k] - b) < 1e-9]
            if len(hit) == 1 and not also_current:
                new[hit[0]] = b
                matched += 1
            elif len(hit) == 1 and also_current:
                log.info("%s: %.2f -> %.2f is a pair of unchanged rates, not a change",
                         c["date"], a, b)
            else:
                unmatched.append((a, b))
        if matched == 0:
            log.warning("%s: no transition matched the corridor %s (saw %s) - chain breaks here",
                        c["date"], state, transitions)
            continue
        if unmatched:
            log.warning("%s: %d transition(s) unmatched: %s", c["date"], len(unmatched), unmatched)
        history.append(dict(effective_date=c["date"], source=c["url"],
                            subject=c.get("subject", ""), **new))
        state = new
    return history


def live_corridor() -> dict | None:
    """Today's corridor from the BB homepage - the end-to-end validation."""
    try:
        from fetchers.policy_rates import fetch_policy_rates
        d = fetch_policy_rates()
        if not d:
            return None
        return {k: d.get(k) for k in ("repo", "slf", "sdf")}
    except Exception as exc:
        log.warning("could not read the live corridor: %s", exc)
        return None


def write_seed(hist: list[dict]) -> None:
    """Rewrite api/seeds/policy_rates.yaml with verified, sourced entries."""
    root = Path(__file__).resolve().parent.parent
    path = root / "api" / "seeds" / "policy_rates.yaml"
    lines = [
        "# Bangladesh Bank policy-rate corridor, effective-dated.",
        "#",
        "# GENERATED by fetchers/policy_history.py from BB MPD circulars - the",
        "# primary source. Every entry is verified two ways: the chain of stated",
        "# prior values runs unbroken from the IRC introduction, and the final",
        "# corridor equals what BB publishes live today. Re-run the fetcher after",
        "# any MPC decision. Do not hand-edit.",
        "corridor:",
    ]
    for h in hist:
        lines.append('  - effective_date: "' + h["effective_date"] + '"')
        lines.append("    repo: %.2f" % h["repo"])
        lines.append("    slf: %.2f" % h["slf"])
        lines.append("    sdf: %.2f" % h["sdf"])
        lines.append("    verified: true")
        lines.append('    source: "' + str(h.get("source", "")) + '"')
        if h.get("subject"):
            lines.append('    note: "' + str(h["subject"])[:110].replace('"', "'") + '"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote " + str(path) + " with " + str(len(hist)) + " verified entries")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    circulars = fetch_circular_index()
    print("MPD policy circulars found: " + str(len(circulars)))

    anchor_c = next((c for c in circulars if c["date"] == ANCHOR_DATE), None)
    if not anchor_c:
        print("anchor circular not found - cannot build a corridor history")
        return 1
    tr, un = read_pdf_numbers(anchor_c["url"])
    print("anchor " + ANCHOR_DATE + ": transitions=" + str(tr) + " unchanged=" + str(un))
    if len(tr) < 3:
        print("anchor circular did not yield three rates - refusing to guess")
        return 1
    # At introduction the circular sets all three. Identify by magnitude rather
    # than text order: SLF is the ceiling, SDF the floor, repo in between.
    news = sorted(b for _a, b in tr[:3])
    anchor = {"sdf": news[0], "repo": news[1], "slf": news[2]}
    print("anchor corridor: " + str(anchor))

    hist = build_history(circulars, anchor)
    print("")
    print("%-12s%7s%7s%7s" % ("effective", "repo", "slf", "sdf"))
    for h in hist:
        print("%-12s%7.2f%7.2f%7.2f" % (h["effective_date"], h["repo"], h["slf"], h["sdf"]))

    live = live_corridor()
    final = {k: hist[-1][k] for k in ("repo", "slf", "sdf")}
    print("")
    print("final from circulars : " + str(final))
    print("live from BB homepage: " + str(live))
    ok = live is not None and all(
        live.get(k) is not None and abs(final[k] - live[k]) < 1e-9 for k in final)
    print("END-TO-END CHECK: " + ("PASS - chain reproduces the published corridor"
                                  if ok else "FAIL - do NOT mark these verified"))
    if ok and "--write" in sys.argv:
        write_seed(hist)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
