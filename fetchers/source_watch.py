"""Watch the SOURCE, not only the data.

Everything else in this project notices data that stops arriving. The worse
case is data that keeps arriving and is wrong, and that case was already latent
in the code: the treasury parser collected BB's real header texts under the
comment "Locate column indices by keyword scan of header text" and then mapped
columns POSITIONALLY, branched on cell count (`if n >= 14`). Insert one column
upstream and that branch still matches — every field is then read from the
wrong index. No freshness check can see it, because nothing is missing and
nothing is stale. The numbers are simply someone else's.

So this module does two things.

1. `header_columns` + `map_columns` map a table by WHAT BB CALLS each column.
   A required header that is missing raises `StructureChanged`, which names
   what moved. A refusal is recoverable; a plausible wrong number is not.

2. `fingerprint` + `record` keep a structural registry per source: which
   selector matched, how many columns, the normalised header texts, how many
   rows were seen, parsed and skipped, and why. Each fetch is compared against
   the ESTABLISHED shape, with two tiers:

     * structure changed, parse still clean  -> a dated finding in the digest;
     * structure changed AND the parse degraded (fewer rows, a required header
       gone, an unmapped column, a jump in skips) -> an escalation.

   This is what would have caught `a.pdf-file` -> `a.pdf-list` on the first
   run, eleven days before anyone needed the number: the selector that used to
   match stopped matching, and that fact is recorded whether or not anyone
   thought to look for it.

Read-only on the source. The only thing it writes is its own registry.
"""
import datetime
import hashlib
import json
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("source_watch")


class StructureChanged(RuntimeError):
    """BB's page no longer has a column we require.

    Raised instead of guessing. The message names the missing field and prints
    the headers actually found, so the fix is a one-line spec edit rather than
    an investigation.
    """

    def __init__(self, source: str, missing: Sequence[str], headers: Sequence[str]):
        self.source = source
        self.missing = list(missing)
        self.headers = list(headers)
        super().__init__(
            f"{source}: required column(s) {', '.join(missing)} not found in BB's "
            f"header. Headers present ({len(headers)}): " +
            " | ".join(headers if headers else ["<none>"]))


# ── normalising header text ───────────────────────────────────────────────────

def norm(s: str) -> str:
    """Lowercase, punctuation to spaces, collapsed.

    "Cut off yield (%)", "Cut-off Yield(%)" and "CUT OFF YIELD %" are the same
    column, and BB has printed all three spellings over the years. Matching on
    punctuation would turn a cosmetic edit into an outage.
    """
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def header_columns(header_rows) -> List[str]:
    """Flatten a multi-row header into one text per LEAF column.

    BB's results table is two levels: "Bids accepted" spanning seven columns,
    then the seven sub-names beneath it. Combining them is what distinguishes
    "Bids received / Face value" from "Bids accepted / Face value" — two
    columns with identical sub-names and very different meanings. One is the
    market's demand, the other is the amount sold.

    BB's markup carries colspan but usually NOT rowspan, so a naive grid reads
    the second row's first cell as belonging to column 0 ("Issue date") and
    every mapping after it shifts. Both shapes are handled.
    """
    levels: List[List[Tuple[str, int]]] = []
    for tr in header_rows:
        cells = [(c.get_text(" ", strip=True), max(1, int(c.get("colspan") or 1)))
                 for c in tr.find_all(["th", "td"])]
        if cells:
            levels.append(cells)
    if not levels:
        return []

    groups = levels[0]
    flat = [t for t, cs in groups for _ in range(cs)]

    for level in levels[1:]:
        subs = [t for t, cs in level for _ in range(cs)]
        if len(subs) == len(flat):
            # Proper rowspan markup, or a header repeated at full width.
            flat = [(a + " " + b).strip() if b and norm(b) != norm(a) else a
                    for a, b in zip(flat, subs)]
            continue
        need = sum(cs for _, cs in groups if cs > 1)
        if len(subs) != need:
            # A shape we do not understand. Stop merging rather than invent an
            # alignment: the columns we already have are honest.
            break
        out, k = [], 0
        for t, cs in groups:
            if cs == 1:
                out.append(t)
            else:
                for _ in range(cs):
                    out.append((t + " " + subs[k]).strip())
                    k += 1
        flat = out
    return [norm(t) for t in flat]


# ── mapping columns by what BB calls them ─────────────────────────────────────

class Column:
    """One column we need, described by the words BB uses for it.

    `all_of` must all appear in the normalised header text; `none_of` must not.
    `required` means a missing column is a loud failure instead of a guess.
    """

    def __init__(self, field: str, all_of: Sequence[str],
                 none_of: Sequence[str] = (), required: bool = False,
                 fallbacks: Sequence[Sequence[str]] = ()):
        self.field = field
        self.all_of = [norm(k) for k in all_of]
        self.none_of = [norm(k) for k in none_of]
        self.required = required
        # Looser wordings, tried only when the exact one is absent and only
        # against columns nobody else claimed. BB's older monthly pages print
        # "Face value" without the "Bids accepted" banner above it, and that is
        # still a header match — a weaker one, but never a positional guess.
        self.fallbacks = [[norm(k) for k in group] for group in fallbacks]

    def matches(self, header: str, keys: Sequence[str] = None) -> bool:
        keys = self.all_of if keys is None else keys
        return (all(k in header for k in keys)
                and not any(k in header for k in self.none_of))


def map_columns(headers: Sequence[str], spec: Sequence[Column],
                source: str = "") -> Tuple[Dict[str, int], Dict[str, object]]:
    """Return {field: column index} plus notes about the mapping.

    Raises StructureChanged when a required column is absent. Columns nobody
    claimed are reported, not ignored: a new column is the single best early
    warning that BB has reorganised a page.
    """
    headers = [norm(h) for h in headers]
    idx: Dict[str, int] = {}
    ambiguous: List[str] = []
    claimed: set = set()

    for col in spec:
        hits = [i for i, h in enumerate(headers) if h and col.matches(h)]
        if not hits:
            continue
        if len(hits) > 1:
            ambiguous.append(f"{col.field}->{hits}")
        idx[col.field] = hits[0]
        claimed.add(hits[0])

    fell_back: List[str] = []
    for col in spec:
        if col.field in idx or not col.fallbacks:
            continue
        for keys in col.fallbacks:
            hits = [i for i, h in enumerate(headers)
                    if h and i not in claimed and col.matches(h, keys)]
            if hits:
                idx[col.field] = hits[0]
                claimed.add(hits[0])
                fell_back.append(f"{col.field}<-'{headers[hits[0]]}'")
                break

    missing = [c.field for c in spec if c.required and c.field not in idx]
    if missing:
        raise StructureChanged(source or "source", missing, headers)

    unmapped = [{"index": i, "header": h} for i, h in enumerate(headers)
                if i not in claimed and h]
    return idx, {"unmapped": unmapped, "ambiguous": ambiguous,
                 "fell_back": fell_back, "header_count": len(headers)}


# ── the structural registry ───────────────────────────────────────────────────

def fingerprint(source: str, url: str = "", selector: str = "",
                headers: Sequence[str] = (), rows_seen: int = 0,
                rows_parsed: int = 0, skips: Optional[Dict[str, int]] = None,
                unmapped: Optional[List[dict]] = None,
                mapped: Optional[Dict[str, int]] = None,
                corrections: Optional[Dict[str, int]] = None,
                error: str = "") -> dict:
    """One fetch, described structurally."""
    headers = [norm(h) for h in headers]
    shape = json.dumps({"selector": selector, "n": len(headers), "h": headers},
                       sort_keys=True)
    skips = dict(skips or {})
    return {
        "source": source,
        "url": url,
        "selector": selector,
        "structure_hash": hashlib.sha256(shape.encode()).hexdigest()[:40],
        "column_count": len(headers),
        "headers": headers,
        "rows_seen": rows_seen,
        "rows_parsed": rows_parsed,
        "rows_skipped": sum(skips.values()),
        "skip_reasons": skips,
        "unmapped": list(unmapped or []),
        "mapped": dict(mapped or {}),
        # Rows the parser KEPT after resolving a disagreement in the source,
        # deliberately NOT counted in rows_skipped: nothing was dropped, and
        # folding them in would push the skip rate over its threshold and
        # report a page full of re-issues as "degraded".
        "corrections": dict(corrections or {}),
        "error": error,
    }


def _skip_rate(snap: dict) -> float:
    seen = snap.get("rows_seen") or 0
    return (snap.get("rows_skipped") or 0) / seen if seen else 0.0


def compare(prev: Optional[dict], cur: dict) -> Tuple[str, List[str]]:
    """Two tiers, deliberately.

    A cosmetic header edit must NOT page anyone — an alarm that fires on
    harmless change is how the eleven-day outage became invisible in the first
    place. But a structural change that costs rows is the loudest signal this
    system has, because it is the only one that appears BEFORE the data is
    wrong rather than after.
    """
    reasons: List[str] = []

    if cur.get("error"):
        return "degraded", [f"the parse failed outright: {cur['error']}"]

    if prev is None:
        if cur.get("rows_parsed", 0) == 0 and cur.get("rows_seen", 0) > 0:
            return "degraded", [f"first look at this source and it parsed none of "
                                f"{cur['rows_seen']} rows"]
        return "ok", []

    structural: List[str] = []
    if cur.get("selector") != prev.get("selector"):
        structural.append(f"the selector that matched changed: "
                          f"{prev.get('selector') or '-'} -> {cur.get('selector') or '-'}")
    if cur.get("column_count") != prev.get("column_count"):
        structural.append(f"column count {prev.get('column_count')} -> "
                          f"{cur.get('column_count')}")
    gone = [h for h in (prev.get("headers") or []) if h not in (cur.get("headers") or [])]
    new = [h for h in (cur.get("headers") or []) if h not in (prev.get("headers") or [])]
    if gone:
        structural.append("header(s) gone: " + "; ".join(gone[:6]))
    if new:
        structural.append("header(s) new: " + "; ".join(new[:6]))
    for f, i in (prev.get("mapped") or {}).items():
        j = (cur.get("mapped") or {}).get(f)
        if j is not None and j != i:
            structural.append(f"{f} moved from column {i} to {j} "
                              f"(read by header, so the figures are still right)")
    if cur.get("unmapped") and len(cur["unmapped"]) > len(prev.get("unmapped") or []):
        structural.append("column(s) nobody claims: " +
                          "; ".join(u["header"] for u in cur["unmapped"][:6]))

    degraded: List[str] = []
    pp, cp = prev.get("rows_parsed") or 0, cur.get("rows_parsed") or 0
    if pp >= 3 and cp == 0:
        degraded.append(f"parsed 0 rows where this source last parsed {pp}")
    elif pp >= 4 and cp < pp * 0.5:
        degraded.append(f"parsed {cp} rows, less than half the {pp} last seen")
    pr, cr = _skip_rate(prev), _skip_rate(cur)
    if (cur.get("rows_seen") or 0) >= 5 and cr > 0.25 and cr > pr + 0.2:
        degraded.append(f"skipped {cr:.0%} of the page, up from {pr:.0%} — "
                        f"reasons: {cur.get('skip_reasons')}")

    if degraded:
        return "degraded", degraded + structural
    if structural:
        return "changed", structural
    return "ok", []


# When a new shape stops being news and becomes BB's normal. Both must hold,
# and the days matter more than the count: one refresh reads eight monthly
# pages, so "three sightings" alone would let a change settle in before the
# morning digest ever mentioned it. Persisting across days is the evidence
# that BB meant it.
SETTLE_SIGHTINGS = 3
SETTLE_DAYS = 2


def established(session, source: str) -> Optional[dict]:
    """The shape BB has held long enough to count as normal.

    Not "last time": comparing against the previous fetch lets a broken shape
    become the baseline on its second run, which is exactly how a silent change
    settles in unnoticed. Not "most sightings" either, because a deliberate BB
    redesign would then be reported as a change for as many runs as the old
    shape was ever seen. The most RECENT settled shape is the honest baseline,
    and a shape that ever parsed badly is never a baseline at all.
    """
    from sqlalchemy import text as _t
    rows = session.execute(_t(
        "SELECT selector, column_count, headers, rows_seen, rows_parsed, "
        "       rows_skipped, skip_reasons, unmapped, mapped, structure_hash, "
        "       seen_count, first_seen, last_seen "
        "FROM source_snapshots WHERE source = :s AND verdict <> 'degraded' "
        "ORDER BY last_seen DESC"), {"s": source}).fetchall()
    if not rows:
        return None

    def _j(v, d):
        try:
            return json.loads(v) if v else d
        except Exception:
            return d

    def _ts(v):
        # Raw SQL over SQLite hands back strings where Postgres hands back
        # datetimes. Subtracting strings raises, and a bare except here would
        # have quietly made every shape "settled".
        if v is None or hasattr(v, "year"):
            return v
        try:
            return datetime.datetime.fromisoformat(str(v)[:26])
        except ValueError:
            return None

    for r in rows:
        if (r[10] or 0) < SETTLE_SIGHTINGS:
            continue
        first, last = _ts(r[11]), _ts(r[12])
        if first and last and (last - first).days < SETTLE_DAYS:
            continue
        return {"selector": r[0], "column_count": r[1], "headers": _j(r[2], []),
                "rows_seen": r[3], "rows_parsed": r[4], "rows_skipped": r[5],
                "skip_reasons": _j(r[6], {}), "unmapped": _j(r[7], []),
                "mapped": _j(r[8], {}), "structure_hash": r[9],
                "seen_count": r[10]}
    return None


def record(session, snap: dict, now: datetime.datetime = None) -> Tuple[str, List[str]]:
    """Compare this fetch to the established shape and register it.

    One row per distinct shape, not per fetch: a shape seen 400 times is the
    known-good one, and that count is the evidence. Returns the verdict so the
    caller can escalate without a second query.
    """
    from db import SourceSnapshot
    now = now or datetime.datetime.utcnow()
    prev = established(session, snap["source"])
    verdict, reasons = compare(prev, snap)

    row = session.query(SourceSnapshot).filter_by(
        source=snap["source"], structure_hash=snap["structure_hash"]).one_or_none()
    if row is None:
        row = SourceSnapshot(source=snap["source"], structure_hash=snap["structure_hash"],
                             first_seen=now, seen_count=0)
        session.add(row)
    row.url = snap.get("url")
    row.selector = snap.get("selector")
    row.column_count = snap.get("column_count")
    row.headers = json.dumps(snap.get("headers") or [])
    row.rows_seen = snap.get("rows_seen")
    row.rows_parsed = snap.get("rows_parsed")
    row.rows_skipped = snap.get("rows_skipped")
    row.skip_reasons = json.dumps({**(snap.get("skip_reasons") or {}),
                                   **({"(corrections)": snap["corrections"]}
                                      if snap.get("corrections") else {})})
    row.unmapped = json.dumps(snap.get("unmapped") or [])
    row.mapped = json.dumps(snap.get("mapped") or {})
    row.verdict = verdict
    row.reasons = json.dumps(reasons)
    row.last_seen = now
    row.seen_count = (row.seen_count or 0) + 1

    if verdict != "ok":
        log.warning("source %s: %s — %s", snap["source"], verdict, "; ".join(reasons))
    return verdict, reasons
