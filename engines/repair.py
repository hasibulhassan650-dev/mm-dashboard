"""
engines/repair.py — self-healing pass for the deterministic breakage classes.

Detection alone is not enough: in Sep-2026 the integrity gate correctly flagged
32 bad yield rows and they sat there for two weeks, because every fix needed a
human. This pass runs at the start of every refresh and repairs the classes
whose correct value is *derivable with certainty* from data we already hold:

  * a yield row with no issue_date          → derive it from the auction date
  * a yield row dated on a non-working day  → move it to the real trading day
  * a coupon/maturity payment on a closed day → re-roll under the live calendar
  * a PLANNED auction settling on a closed day → re-derive T+1

Anything ambiguous (a move that would collide with an existing row, a date the
calendar cannot resolve) is left ALONE and reported, so a human sees it instead
of the code guessing. Every change is logged with its before/after.

Precedent: the coupon self-heal already inside engines/pipeline.py::_upsert_coupon.
"""
import datetime
import logging

import calendar_utils
from db import PrimaryYieldSnapshot, CouponEvent, MaturityEvent, AuctionEvent

log = logging.getLogger(__name__)


def _last_working_day_on_or_before(d: datetime.date) -> datetime.date:
    """The trading day a date belongs to: itself, or the working day before it."""
    return calendar_utils.previous_working_day(d + datetime.timedelta(days=1))


def self_heal(session, limit_log: int = 40) -> dict:
    """Repair what can be derived with certainty. Returns a report dict."""
    repaired: dict = {}
    skipped: list = []
    changes: list = []

    def _did(cls: str, msg: str):
        repaired[cls] = repaired.get(cls, 0) + 1
        if len(changes) < limit_log:
            changes.append(f"{cls}: {msg}")

    # ── 1 & 2. yield rows: the trading day, then the issue date ───────────────
    # BB's results page prints the ISSUE date; auction_date is the working day
    # before it. A writer that skipped the canonical upsert (reconcile.py, until
    # Sep-2026) left issue_date NULL and — because it never loaded the holiday
    # calendar — parked auction_date on closures like Victory Day.
    rows = session.query(PrimaryYieldSnapshot).filter(
        PrimaryYieldSnapshot.auction_date.isnot(None)).all()
    taken = {(r.tenor_label, r.auction_date) for r in rows}
    by_key = {(r.tenor_label, r.auction_date): r for r in rows}
    for r in rows:
        ad = r.auction_date
        try:
            if not calendar_utils.is_working_day(ad):
                true_ad = _last_working_day_on_or_before(ad)
                twin = by_key.get((r.tenor_label, true_ad))
                if twin is not None:
                    # The same auction stored twice: once on its real trading day
                    # and once on a closure, because the writer computed the date
                    # with no holiday calendar loaded. Identical cut-off proves
                    # they are one auction, so drop the bogus copy — two points
                    # for one auction also double-plots the yield chart.
                    same = (twin.cutoff_yield_pct is not None and r.cutoff_yield_pct is not None
                            and abs(twin.cutoff_yield_pct - r.cutoff_yield_pct) < 1e-9)
                    if same:
                        _did("yield_duplicate_removed",
                             f"{r.tenor_label} {ad} dropped — duplicate of {true_ad} @{r.cutoff_yield_pct}")
                        taken.discard((r.tenor_label, ad))
                        session.delete(r)
                        continue
                    skipped.append(f"yield {r.tenor_label} {ad}: real trading day {true_ad} holds a "
                                   f"DIFFERENT cut-off ({twin.cutoff_yield_pct} vs {r.cutoff_yield_pct}) "
                                   f"— needs a human")
                    continue
                taken.discard((r.tenor_label, ad))
                taken.add((r.tenor_label, true_ad))
                _did("yield_auction_date", f"{r.tenor_label} {ad} -> {true_ad} (was a closed day)")
                r.auction_date = ad = true_ad
            if r.issue_date is None:
                r.issue_date = calendar_utils.get_next_working_day(
                    ad + datetime.timedelta(days=1))["result_date"]
                _did("yield_issue_date", f"{r.tenor_label} {ad} issue_date -> {r.issue_date}")
        except Exception as exc:                       # calendar cannot resolve it
            skipped.append(f"yield {r.tenor_label} {ad}: {exc}")

    # ── 3. coupon / maturity payments must land on a working day ─────────────
    for model, label in ((CouponEvent, "coupon"), (MaturityEvent, "maturity")):
        for e in session.query(model).filter(model.scheduled_date.isnot(None)).all():
            try:
                want = calendar_utils.get_next_working_day(e.scheduled_date)["result_date"]
            except Exception as exc:
                skipped.append(f"{label} {e.isin} {e.scheduled_date}: {exc}")
                continue
            if e.payment_date != want:
                _did(f"{label}_payment_date", f"{e.isin} {e.scheduled_date}: {e.payment_date} -> {want}")
                e.payment_date = want

    # ── 4. PLANNED auctions settle T+1 working day ───────────────────────────
    # CONFIRMED rows carry BB's actual issue date and must never be recomputed.
    for a in session.query(AuctionEvent).filter(AuctionEvent.auction_date.isnot(None)).all():
        if a.outflow_status == "CONFIRMED":
            continue
        try:
            want = calendar_utils.get_next_working_day(
                a.auction_date + datetime.timedelta(days=1))["result_date"]
        except Exception as exc:
            skipped.append(f"auction {a.tenor_label} {a.auction_date}: {exc}")
            continue
        if a.settlement_date != want:
            _did("auction_settlement", f"{a.tenor_label} {a.auction_date}: {a.settlement_date} -> {want}")
            a.settlement_date = want

    session.flush()
    total = sum(repaired.values())
    if total:
        log.warning("SELF-HEAL repaired %d row(s): %s", total, repaired)
        for c in changes:
            log.info("  repaired %s", c)
    if skipped:
        log.warning("SELF-HEAL left %d row(s) for review: %s", len(skipped), skipped[:10])
    return {"repaired": repaired, "total": total, "skipped": skipped, "changes": changes}


if __name__ == "__main__":          # python -m engines.repair
    import json
    from db import get_session, init_db
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    s = get_session()
    try:
        rep = self_heal(s)
        s.commit()
        print(json.dumps(rep, indent=2, default=str))
    finally:
        s.close()
