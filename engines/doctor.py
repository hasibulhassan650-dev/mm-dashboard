"""The Doctor: repairs the failures this project has already solved by hand - but only with PROOF, and always on the record.

Every repair here is something a person or an agent did manually in Oct-Nov 2026 after reading logs:
  * a day the BB exchange-rate page showed while no read landed is gone for good (the page keeps only the latest day):
    written down as a `lost` absence from OUR OWN read log (25-Oct, 29-Oct, 5-Nov, 10-Nov were hand-written YAML);
  * a tranche whose printed tenor BB's own later maturity print contradicts (25-Oct CB Repo, 28-Oct Assured Repo: twice in
    three weeks): corrected when - and only when - the profit arithmetic reproduces the print to the paisa, uniquely.

Rules (owner's decision, 16-Nov-2026: "auto-fix with proof"):
  1. PROOF or nothing. Arithmetic on BB's own figures, or our own read timestamps. Ambiguity is reported, never resolved by a guess.
  2. Every change is recorded in data_changes WITH the old values, so it can be reverted (`revert_tenor_autoheal`).
  3. A cap per run (MAX_TENOR_FIXES_PER_RUN) so a bug in the proof can never rewrite the ledger wholesale.
  4. Idempotent: running it twice changes nothing the second time.
  5. It writes a heartbeat (step 'doctor' in step_runs); validate flags a Doctor that has not run.

Pure detection functions are separate from the database glue so the proofs are tested on fixtures, never on live data.
"""
import datetime
import json
from typing import Dict, Iterable, List, Optional, Set, Tuple

MAX_TENOR_FIXES_PER_RUN = 3
BGIIB_GRACE_DAYS = 5               # a listed BGIIB PDF BB does not serve is BB's hole only after this many days...
BGIIB_MIN_FAILED_READS = 3         # ...and this many failed reads, and only while BB serves a LATER BGIIB PDF
UNSERVED_MARK = "Unserved PDF:"    # the Doctor's own absences start with this, so it can find (and retire) exactly its own
TENOR_PROOF_TOLERANCE_CRORE = 0.02          # paisa-exact: BB prints 2 decimals
LEDGER_TOLERANCE_PCT, LEDGER_TOLERANCE_MIN = 0.015, 10.0     # the same band validate's omo-ledger uses
MAX_TENOR_DAYS = 400


# ── lost days ────────────────────────────────────────────────────────────────
def find_lost_days(first_read: Dict[datetime.date, datetime.datetime], holidays: Set[datetime.date],
                   already_recorded: Iterable[datetime.date] = (), is_working_day=None) -> List[Dict]:
    """Working days INSIDE a held series that we never read, for a page that keeps only its latest day.

    `first_read` maps each held date to when we first stored it. A missing working day D is provably unrecoverable only when
    the series moved PAST it: the day before and the day after are both held, and the later one was read later (so the page
    has shown the next day since). Without a later neighbour the page may still be showing D, so nothing is concluded."""
    if is_working_day is None:
        import calendar_utils
        is_working_day = calendar_utils.is_working_day
    held = sorted(first_read)
    if len(held) < 2:
        return []
    skip = set(already_recorded)
    out = []
    d = held[0]
    while d <= held[-1]:
        if d not in first_read and d not in skip and is_working_day(d, holidays):
            prev = max((x for x in held if x < d), default=None)
            nxt = min((x for x in held if x > d), default=None)
            if prev and nxt and first_read[nxt] > first_read[prev]:
                out.append({"day": d, "prev_day": prev, "prev_read": first_read[prev],
                            "next_day": nxt, "next_read": first_read[nxt]})
        d += datetime.timedelta(days=1)
    return out


def lost_reason(f: Dict) -> str:
    return (f"Not read while the page described {f['day']} (held: {f['prev_day']} read on {str(f['prev_read'])[:16]} UTC, "
            f"{f['next_day']} read on {str(f['next_read'])[:16]} UTC); BB's page keeps only the latest day, so it cannot be "
            f"re-read. Recorded automatically by the Doctor from our own read log.")


# ── tenor mismatches ─────────────────────────────────────────────────────────
def _profit_amount(accepted: float, rate_pct: float, days: int) -> float:
    return accepted * (1 + (rate_pct or 0) / 100 * days / 365)


def find_tenor_mismatches(printed: Dict[Tuple[str, datetime.date], float], tranches: List[Dict],
                          skip_instruments: Iterable[str] = ("MLS",)) -> List[Dict]:
    """Tranches whose stored tenor BB's own maturity print contradicts, with the proof.

    printed  - {(instrument, date): the maturity BB printed that day} (principal PLUS profit, summed per instrument/day)
    tranches - dicts: id, instrument, deal_date, accepted, rate_pct, tenor_days, maturity_date

    For each (instrument, day) where BB printed MORE maturity than the tranches we hold there explain, the shortfall must be
    reproduced - within a paisa - by exactly ONE stored tranche of that instrument treated as maturing that day instead (its
    principal plus (day - deal) days of profit at its own rate). Two candidates, or none, and nothing is concluded."""
    skip = set(skip_instruments)
    live: Dict[Tuple[str, datetime.date], float] = {}
    for t in tranches:
        if t["accepted"] and t["accepted"] > 0 and t["maturity_date"]:
            k = (t["instrument"], t["maturity_date"])
            live[k] = live.get(k, 0.0) + _profit_amount(t["accepted"], t["rate_pct"], t["tenor_days"] or 0)
    out = []
    for (inst, day), bb in sorted(printed.items(), key=lambda x: (x[0][1], x[0][0])):
        if inst in skip:
            continue
        ours = live.get((inst, day), 0.0)
        gap = bb - ours
        if gap <= max(LEDGER_TOLERANCE_PCT * bb, LEDGER_TOLERANCE_MIN):
            continue                                  # no shortfall to explain
        cands = []
        for t in tranches:
            if t["instrument"] != inst or not t["accepted"] or t["accepted"] <= 0 or t["maturity_date"] == day:
                continue
            days = (day - t["deal_date"]).days
            if not 1 <= days <= MAX_TENOR_DAYS:
                continue
            if abs(_profit_amount(t["accepted"], t["rate_pct"], days) - gap) <= TENOR_PROOF_TOLERANCE_CRORE:
                cands.append((t, days))
        if len(cands) == 1:
            t, days = cands[0]
            out.append({"id": t["id"], "instrument": inst, "deal_date": t["deal_date"], "accepted_crore": t["accepted"],
                        "old_tenor_days": t["tenor_days"], "old_maturity": t["maturity_date"], "true_maturity": day,
                        "true_tenor_days": days,
                        "proof": {"printed_crore": round(bb, 2), "held_crore": round(ours, 2), "gap_crore": round(gap, 2),
                                  "reproduced_crore": round(_profit_amount(t["accepted"], t["rate_pct"], days), 2),
                                  "rate_pct": t["rate_pct"]}})
    # a tranche must be explained once: if two different days both claim it, neither is trusted
    seen: Dict[int, int] = {}
    for f in out:
        seen[f["id"]] = seen.get(f["id"], 0) + 1
    return [f for f in out if seen[f["id"]] == 1]


# ── a listed PDF BB never serves ─────────────────────────────────────────────
def find_unserved_bgiib(releases: List[Dict], stored_dates: Iterable[datetime.date], failed_reads: Dict[str, List],
                        served_serials: Iterable[str], today: datetime.date) -> List[Dict]:
    """BGIIB results BB has LISTED but whose PDF it demonstrably does not serve - proof, not a guess.

    A release qualifies only when ALL hold: no auction is stored within 2 days of its held date; it is at least BGIIB_GRACE_DAYS
    old; at least BGIIB_MIN_FAILED_READS reads of it failed; and a LATER BGIIB release has been read successfully (BB serves PDFs,
    so this one is BB's dead link, not an outage or our block). Anything less stays a waiting item that retries every run.

    releases      - dicts: serial, published (date), held (date), url
    failed_reads  - {serial: [timestamps of failed reads]} from the step ledger
    served_serials- serials of BGIIB releases whose figures we hold"""
    stored = list(stored_dates)
    served = set(served_serials)
    out = []
    for r in releases:
        if any(abs((d - r["held"]).days) <= 2 for d in stored):
            continue
        reads = sorted(failed_reads.get(r["serial"], []))
        if (today - r["published"]).days < BGIIB_GRACE_DAYS or len(reads) < BGIIB_MIN_FAILED_READS:
            continue
        later = sorted(x["serial"] for x in releases if x["published"] > r["published"] and x["serial"] in served)
        if not later:
            continue
        out.append({"serial": r["serial"], "held": r["held"], "url": r["url"], "reads": len(reads),
                    "first_read": reads[0], "last_read": reads[-1], "later_served": later[0]})
    return out


def unserved_reason(f: Dict) -> str:
    return (f"{UNSERVED_MARK} BB lists {f['serial']} (BGIIB result held {f['held']}) at {f['url']} but has not served its PDF in "
            f"{f['reads']} reads between {str(f['first_read'])[:16]} and {str(f['last_read'])[:16]} UTC, while it did serve the later "
            f"release {f['later_served']}; BB's dead link, retried every run and retired automatically when BB publishes it. "
            f"Recorded by the Doctor.")


# ── database glue ────────────────────────────────────────────────────────────
def _holidays(session) -> Set[datetime.date]:
    from db import HolidayCalendar
    return {r[0] for r in session.query(HolidayCalendar.calendar_date).all()}


def heal_lost_days(session, now: Optional[datetime.datetime] = None) -> List[str]:
    """Record `lost` absences for the exchange-rate series from our own read log. Returns what was recorded."""
    from sqlalchemy import func

    from db import DataAbsence, FxRateDaily
    from engines.absences import record_loss
    now = now or datetime.datetime.utcnow()
    rows = session.query(FxRateDaily.rate_date, func.min(FxRateDaily.ingested_utc)).group_by(FxRateDaily.rate_date).all()
    first_read = {d: t for d, t in rows if d and t}
    known = {r[0] for r in session.query(DataAbsence.absent_date).filter(DataAbsence.dataset == "fxrates").all()}
    done = []
    for f in find_lost_days(first_read, _holidays(session), known):
        if record_loss(session, "fxrates", f["day"], lost_reason(f),
                       "https://www.bb.org.bd/en/index.php/econdata/exchangerate", now):
            session.add(_change(now, "lost_day", {"dataset": "fxrates", "day": str(f["day"]), "evidence": lost_reason(f)}))
            done.append(f"fxrates {f['day']} (lost)")
    return done


def heal_unserved_bgiib(session, now: Optional[datetime.datetime] = None) -> Dict[str, List[str]]:
    """Record a BGIIB result BB lists but does not serve as BB's hole, with evidence; retire the record when BB publishes it.

    The PDF keeps being retried by run_bgiib_fetch every run, so a fixed link is picked up with no human: the next run stores the
    figures and this function then removes its own absence (data_changes keeps both events)."""
    from db import BgiibAuction, DataAbsence, PressRelease, StepRun
    now = now or datetime.datetime.utcnow()
    today = now.date()
    out: Dict[str, List[str]] = {"recorded": [], "retired": []}
    stored = {}
    for d, serial in session.query(BgiibAuction.auction_date, BgiibAuction.source_serial).all():
        stored[serial] = d
    # retire first: figures now exist near a date the Doctor had recorded as BB's hole
    for a in session.query(DataAbsence).filter(DataAbsence.dataset == "bgiib").all():
        if (a.reason or "").startswith(UNSERVED_MARK) and any(abs((d - a.absent_date).days) <= 2 for d in stored.values()):
            session.add(_change(now, "unserved_pdf_retired", {"day": str(a.absent_date), "why": "BB published the figures"}))
            out["retired"].append(f"bgiib {a.absent_date} (BB published it)")
            session.delete(a)
    rels = []
    for serial, pub, subj, url in session.query(PressRelease.serial, PressRelease.published_date, PressRelease.subject_date,
                                                PressRelease.url).filter(
            PressRelease.category == "BGIIB", ~PressRelease.title.ilike("%notice%"),
            PressRelease.published_date >= today - datetime.timedelta(days=60)).all():
        held = subj if isinstance(subj, datetime.date) else pub
        if held is not None and pub is not None:
            rels.append({"serial": serial, "published": pub, "held": held, "url": url})
    failed: Dict[str, List] = {}
    for r in session.query(StepRun).filter(StepRun.step == "bgiib", StepRun.status != "ok",
                                           StepRun.run_utc >= now - datetime.timedelta(days=30)).all():
        for rel in rels:
            if rel["serial"] in (r.error or ""):
                failed.setdefault(rel["serial"], []).append(r.run_utc)
    known = {a.absent_date for a in session.query(DataAbsence).filter(DataAbsence.dataset == "bgiib").all()}
    for f in find_unserved_bgiib(rels, stored.values(), failed, [s for s in stored if s], today):
        if any(abs((k - f["held"]).days) <= 2 for k in known):
            continue
        session.add(DataAbsence(dataset="bgiib", absent_date=f["held"], kind="bb_omission", reason=unserved_reason(f),
                                source=f["url"], verified_utc=now, checks=1))
        session.add(_change(now, "unserved_pdf", {"serial": f["serial"], "held": str(f["held"]), "reads": f["reads"],
                                                  "later_served": f["later_served"], "evidence": unserved_reason(f)}))
        out["recorded"].append(f"bgiib {f['held']} ({f['serial']}: BB dead link)")
    return out


def _change(now, cls: str, detail: Dict):
    from db import DataChange
    return DataChange(changed_utc=now, change_class=cls, detail=json.dumps(detail, default=str))


def heal_tenors(session, now: Optional[datetime.datetime] = None, window_days: int = 45,
                cap: int = MAX_TENOR_FIXES_PER_RUN) -> List[Dict]:
    """Correct tranches whose tenor BB's own later maturity print contradicts, with proof; at most `cap` per run."""
    from sqlalchemy import text

    from db import OMOTransaction, TenorOverride
    now = now or datetime.datetime.utcnow()
    today = now.date()
    lo, hi = today - datetime.timedelta(days=window_days), today - datetime.timedelta(days=2)
    printed: Dict[Tuple[str, datetime.date], float] = {}
    for inst, d, m in session.execute(text(
            "SELECT instrument, transaction_date, SUM(COALESCE(maturity_bdt_crore, 0)) FROM omo_transactions "
            "WHERE transaction_date BETWEEN :lo AND :hi GROUP BY instrument, transaction_date"), {"lo": lo, "hi": hi}):
        if m and float(m) > 0:
            d = d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d)[:10])
            printed[(inst, d)] = float(m)
    rows = session.query(OMOTransaction).filter(OMOTransaction.accepted_bdt_crore > 0,
                                                OMOTransaction.transaction_date >= lo - datetime.timedelta(days=MAX_TENOR_DAYS)).all()
    tranches = [{"id": r.id, "instrument": r.instrument, "deal_date": r.transaction_date, "accepted": r.accepted_bdt_crore,
                 "rate_pct": r.rate_pct, "tenor_days": r.tenor_days, "maturity_date": r.maturity_date} for r in rows]
    by_id = {r.id: r for r in rows}
    applied = []
    for f in find_tenor_mismatches(printed, tranches):
        if len(applied) >= cap:
            break
        row = by_id[f["id"]]
        row.tenor_days, row.maturity_date = f["true_tenor_days"], f["true_maturity"]
        session.add(TenorOverride(deal_date=f["deal_date"], instrument=f["instrument"], accepted_crore=f["accepted_crore"],
                                  true_maturity=f["true_maturity"], source="doctor", created_utc=now,
                                  evidence=json.dumps(f["proof"])))
        session.add(_change(now, "tenor_autoheal", {
            "row_id": f["id"], "instrument": f["instrument"], "deal_date": str(f["deal_date"]),
            "accepted_crore": f["accepted_crore"], "old_tenor_days": f["old_tenor_days"],
            "old_maturity": str(f["old_maturity"]), "new_tenor_days": f["true_tenor_days"],
            "new_maturity": str(f["true_maturity"]), "proof": f["proof"]}))
        applied.append(f)
    return applied


def revert_tenor_autoheal(session, change_id: int) -> bool:
    """Undo one automatic tenor correction from its data_changes record (old values are kept for exactly this)."""
    from db import DataChange, OMOTransaction, TenorOverride
    ch = session.get(DataChange, change_id)
    if ch is None or ch.change_class != "tenor_autoheal":
        return False
    d = json.loads(ch.detail)
    row = session.get(OMOTransaction, d["row_id"])
    if row is not None:
        row.tenor_days = d["old_tenor_days"]
        row.maturity_date = datetime.date.fromisoformat(d["old_maturity"])
    session.query(TenorOverride).filter_by(deal_date=datetime.date.fromisoformat(d["deal_date"]), instrument=d["instrument"],
                                           accepted_crore=d["accepted_crore"], source="doctor").delete()
    ch.change_class = "tenor_autoheal_reverted"
    return True


def run_doctor(session, now: Optional[datetime.datetime] = None) -> Dict:
    """One pass: repair what has proof, heartbeat, and report. Never raises into the caller's run."""
    from db import StepRun
    now = now or datetime.datetime.utcnow()
    out: Dict = {"lost_days": [], "tenor_fixes": [], "unserved": {"recorded": [], "retired": []}, "errors": []}
    try:
        out["lost_days"] = heal_lost_days(session, now)
    except Exception as exc:
        session.rollback()
        out["errors"].append(f"doctor: lost-day repair failed: {exc}")
    try:
        out["tenor_fixes"] = heal_tenors(session, now)
    except Exception as exc:
        session.rollback()
        out["errors"].append(f"doctor: tenor repair failed: {exc}")
    try:
        out["unserved"] = heal_unserved_bgiib(session, now)
    except Exception as exc:
        session.rollback()
        out["errors"].append(f"doctor: unserved-PDF repair failed: {exc}")
    from fetchers.news import machine
    session.add(StepRun(run_utc=now, machine=machine(), step="doctor",
                        status="failed" if out["errors"] else "ok", error="; ".join(out["errors"]) or None,
                        rows_stored=len(out["lost_days"]) + len(out["tenor_fixes"]) + len(out["unserved"]["recorded"]),
                        evidence_json=json.dumps({"lost_days": out["lost_days"],
                                                  "tenor_fixes": [str(f["id"]) for f in out["tenor_fixes"]]})))
    return out
