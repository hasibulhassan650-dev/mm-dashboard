"""
reconcile.py — periodic cross-check of stored treasury-auction history against
a fresh re-fetch from Bangladesh Bank. Makes the dataset self-correcting:

  * fills genuine gaps (rows we never captured),
  * corrects clearly-wrong stored values (e.g. a stored 0 vs a real yield),
  * applies BB revisions (small positive-vs-positive changes),
  * FLAGS large positive-vs-positive mismatches for human review instead of
    blindly overwriting (these could be a parse error on either side).

Guards (so it never re-introduces known parser artefacts):
  - never store a yield <= 0,
  - never store a sub-1% yield OUTSIDE the genuine 2020-21 COVID low window.

Runs a recent window (revisions are most likely there) plus a rotating
historical slice, so repeated runs eventually re-verify ALL 19 years.

Usage: python reconcile.py            # recent + rotating slice (cloud default)
       python reconcile.py 2007 2026  # explicit full range
"""
import sys, os, json, datetime, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reconcile")

from sqlalchemy import text
from db import get_session, init_db, PipelineRun
from fetchers.treasury import fetch_primary_yields_months, month_range
from backfill_treasury import fix_sequence

COVID_LO = datetime.date(2020, 9, 1)
COVID_HI = datetime.date(2021, 10, 31)
EPS = 0.005            # ignore tiny float noise
REVISION = 0.50        # |Δ| below this (and plausible) = accept as a BB revision

def plausible(auction_date, cutoff):
    if cutoff is None or cutoff <= 0 or cutoff > 30:
        return False
    if cutoff < 1.0 and not (COVID_LO <= auction_date <= COVID_HI):
        return False    # systematic old-layout parse error
    return True

def windows():
    """recent 12 months + a rotating ~6-year historical slice."""
    today = datetime.date.today()
    recent_start = (today.replace(day=1) - datetime.timedelta(days=365)).replace(day=1)
    slices = [(2007, 2013), (2014, 2019), (2020, today.year)]
    pick = slices[(today.toordinal() // 3) % len(slices)]
    log.info("reconcile windows: recent %s->%s + historical slice %s", recent_start, today, pick)
    months = month_range(recent_start, today)
    months += month_range(datetime.date(pick[0], 1, 1), datetime.date(min(pick[1], today.year), 12, 28))
    seen = set(); uniq = []
    for m, snap in months:
        if m not in seen:
            seen.add(m); uniq.append((m, snap))
    return uniq

def main():
    init_db(); fix_sequence()
    args = sys.argv[1:]
    if len(args) == 2:
        months = month_range(datetime.date(int(args[0]), 1, 1), datetime.date(int(args[1]), 12, 28))
    else:
        months = windows()

    start = datetime.datetime.now()
    stats = {"checked": 0, "inserted": 0, "corrected": 0, "revised": 0, "flagged": 0, "skipped_implausible": 0}
    flagged_samples = []

    CHUNK = 12
    for i in range(0, len(months), CHUNK):
        chunk = months[i:i+CHUNK]
        rows = fetch_primary_yields_months(chunk)
        if not rows:
            continue
        s = get_session()
        now = datetime.datetime.utcnow()
        try:
            for r in rows:
                stats["checked"] += 1
                ad, tl, cy = r["auction_date"], r["tenor_label"], r["cutoff_yield_pct"]
                if not plausible(ad, cy):
                    stats["skipped_implausible"] += 1
                    continue
                ex = s.execute(text(
                    "SELECT id, cutoff_yield_pct FROM primary_yield_snapshots "
                    "WHERE tenor_label=:t AND auction_date=:d"), {"t": tl, "d": ad}).fetchone()
                if ex is None:
                    r["ingested_utc"] = now
                    s.execute(text(
                        "INSERT INTO primary_yield_snapshots (snapshot_date,auction_date,security_type,tenor_label,"
                        "tenor_years,cutoff_yield_pct,offered_bdt_crore,accepted_bdt_crore,source,ingested_utc) "
                        "VALUES (:snapshot_date,:auction_date,:security_type,:tenor_label,:tenor_years,"
                        ":cutoff_yield_pct,:offered_bdt_crore,:accepted_bdt_crore,:source,:ingested_utc)"), r)
                    stats["inserted"] += 1
                else:
                    old = ex[1]
                    if old is None or abs(old - cy) <= EPS:
                        continue
                    if old <= 0 or old > 30 or (old < 1.0 and not (COVID_LO <= ad <= COVID_HI)):
                        # stored value is implausible → correct it
                        s.execute(text("UPDATE primary_yield_snapshots SET cutoff_yield_pct=:c WHERE id=:i"), {"c": cy, "i": ex[0]})
                        stats["corrected"] += 1
                    elif abs(old - cy) < REVISION:
                        s.execute(text("UPDATE primary_yield_snapshots SET cutoff_yield_pct=:c WHERE id=:i"), {"c": cy, "i": ex[0]})
                        stats["revised"] += 1
                    else:
                        stats["flagged"] += 1
                        if len(flagged_samples) < 25:
                            flagged_samples.append(f"{ad} {tl}: stored={old} fetched={cy}")
            s.commit()
        finally:
            s.close()

    elapsed = (datetime.datetime.now() - start).seconds
    log.info("RECONCILE %s | flagged samples: %s", stats, flagged_samples[:25])
    if stats["flagged"]:
        log.warning("%d mismatch(es) flagged for review — re-run a narrower range to investigate.", stats["flagged"])

    # record the run
    try:
        s = get_session()
        s.add(PipelineRun(run_utc=datetime.datetime.utcnow(), kind="reconcile",
                          new_rows=json.dumps(stats), errors=json.dumps(flagged_samples) if flagged_samples else None,
                          elapsed_sec=elapsed))
        s.commit(); s.close()
    except Exception as exc:
        log.warning("could not record reconcile run: %s", exc)

if __name__ == "__main__":
    main()
