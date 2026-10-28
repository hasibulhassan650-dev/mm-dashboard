"""The desk's own cut-off, entered on auction day.

BB's results page prints the ISSUE date, and the auction was the previous
working day, so a published cut-off reaches this system a day or two after the
desk has already traded off it. The desk is in the auction. This is where it
records what it saw, while it is still useful.

Two rules shape everything here.

**A desk mark is never BB data.** It goes to its own table, is labelled
provisional wherever it appears, and BB's published cut-off is preferred the
moment it exists. The mark is not overwritten when that happens — it stays as
the record of what the desk believed and when, and the gap between the two is
worth reading: 40bp out is either a typo or a surprise in the auction, and the
desk wants to know which.

**The write path fails closed.** This API is public, so the only thing standing
between it and a forged cut-off is a shared secret. If `DESK_WRITE_SECRET` is
unset the route refuses every write — it does not fall back to a default, a
hash baked into a public repo, or "allow when unconfigured". The browser never
sees the secret: the page posts same-origin to the Next app, which holds it
server-side and forwards. CORS stays GET-only and is not widened for this.
"""
import datetime
import re
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from db import get_session

router = APIRouter()

# Tenors BB actually auctions. A free-text label would let a typo create a
# brand-new tenor that then appears on the curve as a real point. The list is the tenors we know
# BB can auction PLUS every tenor already in the auction history (see _known_tenors): a fixed list
# rejected the 14-day bill, and would reject whatever BB introduces next.
_TENORS = {"91D", "182D", "364D", "2Y", "5Y", "10Y", "15Y", "20Y",
           "3Y_FRTB", "5Y_FRTB", "10Y_FRTB"}
_TENOR_SHAPE = re.compile(r"^\d{1,3}[DY](_FRTB)?$")


def _tenor_order(label: str):
    m = re.match(r"^(\d+)([DY])", label)
    return ((int(m.group(1)) / 365 if m.group(2) == "D" else int(m.group(1))) if m else 1e9, label)


def _known_tenors(session) -> set:
    rows = session.execute(text(
        "SELECT DISTINCT tenor_label FROM primary_yield_snapshots WHERE tenor_label IS NOT NULL")).fetchall()
    return _TENORS | {r[0] for r in rows if _TENOR_SHAPE.match(str(r[0]))}


class MarkIn(BaseModel):
    auction_date: datetime.date
    tenor_label: str
    cutoff_yield_pct: float = Field(gt=0, le=30)
    security_type: Optional[str] = None
    accepted_crore: Optional[float] = Field(default=None, ge=0)
    entered_by: Optional[str] = Field(default=None, max_length=60)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("tenor_label")
    @classmethod
    def known_tenor(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not _TENOR_SHAPE.match(v):          # whether BB has auctioned it is checked against the history
            raise ValueError(f"'{v}' is not a tenor label (e.g. 91D, 2Y, 3Y_FRTB)")
        return v

    @field_validator("auction_date")
    @classmethod
    def not_in_the_future(cls, v: datetime.date) -> datetime.date:
        # An auction that has not happened has no cut-off to know. Tomorrow's
        # date is a typo, and a typo here is a figure on the curve.
        if v > datetime.date.today():
            raise ValueError("auction_date is in the future")
        if v < datetime.date.today() - datetime.timedelta(days=30):
            raise ValueError("auction_date is more than 30 days old — BB has "
                             "published by now, so enter nothing and let the "
                             "published result stand")
        return v


class BuybackIn(BaseModel):
    isin: str
    settlement_date: datetime.date
    accepted_face_crore: float = Field(gt=0, le=100000)
    cash_paid_crore: Optional[float] = Field(default=None, ge=0, le=200000)
    price_per_100: Optional[float] = Field(default=None, gt=0, le=200)
    entered_by: Optional[str] = Field(default=None, max_length=60)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("isin")
    @classmethod
    def isin_shape(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not (10 <= len(v) <= 12 and v.isalnum()):
            raise ValueError(f"'{v}' is not an ISIN")
        return v

    @field_validator("settlement_date")
    @classmethod
    def near_today(cls, v: datetime.date) -> datetime.date:
        # A buyback settles days after its auction. A date far from today is a
        # typo, and the window keeps the desk from rewriting a year-old event.
        today = datetime.date.today()
        if not (today - datetime.timedelta(days=30) <= v <= today + datetime.timedelta(days=30)):
            raise ValueError("settlement_date must be within 30 days of today")
        return v


def _authorise(given: Optional[str]) -> None:
    """Fail closed. No secret configured means no writes, ever."""
    want = os.environ.get("DESK_WRITE_SECRET") or ""
    if not want:
        raise HTTPException(
            status_code=503,
            detail="desk entry is not configured: DESK_WRITE_SECRET is unset on the "
                   "API, so writes are refused rather than left open")
    if not given or not hmac.compare_digest(given, want):
        raise HTTPException(status_code=403, detail="bad desk secret")


@router.get("/ping")
def ping(x_desk_secret: Optional[str] = Header(default=None)):
    """Does the secret the SITE holds match the one this API holds?

    Writes nothing. It exists because the desk could not tell, from a form that
    simply refused, WHICH of the two places was missing the secret - the site,
    the API, or both with different values - and "set it in both" is a
    two-minute job that is easy to do half of. Same gate as the write route,
    so it fails closed identically: no secret configured here means 503, a
    wrong one 403, and the response never contains either value.
    """
    _authorise(x_desk_secret)
    return {"ok": True, "configured": True}


@router.post("/mark")
def put_mark(mark: MarkIn, x_desk_secret: Optional[str] = Header(default=None)):
    """Record (or correct) the desk's cut-off for one auction and tenor."""
    _authorise(x_desk_secret)
    session = get_session()
    try:
        known = _known_tenors(session)
        if mark.tenor_label not in known:
            raise HTTPException(status_code=422, detail=f"unknown tenor '{mark.tenor_label}' - BB has never "
                                                       f"auctioned it here; one of {sorted(known)}")
        now = datetime.datetime.utcnow()
        existing = session.execute(text(
            "SELECT id FROM desk_marks WHERE auction_date = :d AND tenor_label = :t"),
            {"d": str(mark.auction_date), "t": mark.tenor_label}).fetchone()
        params = {
            "d": str(mark.auction_date), "t": mark.tenor_label,
            "st": mark.security_type, "c": mark.cutoff_yield_pct,
            "amt": mark.accepted_crore, "by": mark.entered_by,
            "note": mark.note, "now": now,
        }
        if existing:
            session.execute(text(
                "UPDATE desk_marks SET cutoff_yield_pct = :c, security_type = :st, "
                "accepted_crore = :amt, entered_by = :by, note = :note, "
                "updated_utc = :now WHERE id = :id"), {**params, "id": existing[0]})
            action = "updated"
        else:
            session.execute(text(
                "INSERT INTO desk_marks (auction_date, tenor_label, security_type, "
                "  cutoff_yield_pct, accepted_crore, entered_by, note, entered_utc, "
                "  updated_utc) "
                "VALUES (:d, :t, :st, :c, :amt, :by, :note, :now, :now)"), params)
            action = "recorded"
        session.commit()
        return {"ok": True, "action": action, "auction_date": str(mark.auction_date),
                "tenor_label": mark.tenor_label, "cutoff_yield_pct": mark.cutoff_yield_pct}
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"could not store the mark: {exc}")
    finally:
        session.close()


@router.post("/buyback")
def put_buyback(b: BuybackIn, x_desk_secret: Optional[str] = Header(default=None)):
    """Record (or correct) the desk's figure for an announced buyback.

    Same gate as the cut-off mark, and the same principle: provisional, never
    BB data, never in a ladder total. The ISIN must be a security we hold -
    a typo here would otherwise look like a real bond.
    """
    _authorise(x_desk_secret)
    session = get_session()
    try:
        if not session.execute(text("SELECT 1 FROM securities WHERE isin = :i"),
                               {"i": b.isin}).fetchone():
            raise HTTPException(status_code=422,
                                detail=f"{b.isin} is not a security in the master list")
        now = datetime.datetime.utcnow()
        params = {"i": b.isin, "d": str(b.settlement_date), "f": b.accepted_face_crore,
                  "c": b.cash_paid_crore, "p": b.price_per_100, "by": b.entered_by,
                  "note": b.note, "now": now}
        existing = session.execute(text(
            "SELECT id FROM desk_buyback_marks WHERE isin = :i AND settlement_date = :d"),
            params).fetchone()
        if existing:
            session.execute(text(
                "UPDATE desk_buyback_marks SET accepted_face_crore = :f, cash_paid_crore = :c, "
                "price_per_100 = :p, entered_by = :by, note = :note, updated_utc = :now "
                "WHERE id = :id"), {**params, "id": existing[0]})
            action = "updated"
        else:
            session.execute(text(
                "INSERT INTO desk_buyback_marks (isin, settlement_date, accepted_face_crore, "
                "cash_paid_crore, price_per_100, entered_by, note, entered_utc, updated_utc) "
                "VALUES (:i, :d, :f, :c, :p, :by, :note, :now, :now)"), params)
            action = "recorded"
        session.commit()
        return {"ok": True, "action": action, "isin": b.isin,
                "settlement_date": str(b.settlement_date)}
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"could not store the buyback: {exc}")
    finally:
        session.close()


def desk_buybacks(session, since: datetime.date):
    """Desk buyback marks with BB's event beside them, once it exists."""
    rows = session.execute(text("""
        SELECT d.isin, d.settlement_date, d.accepted_face_crore, d.cash_paid_crore,
               d.price_per_100, d.entered_by, d.note, d.updated_utc,
               b.accepted_face_crore AS bb_accepted_face_crore,
               b.cash_paid_crore AS bb_cash_paid_crore
        FROM desk_buyback_marks d
        LEFT JOIN buyback_events b
               ON b.isin = d.isin AND b.settlement_date = d.settlement_date
        WHERE d.settlement_date >= :since
        ORDER BY d.settlement_date DESC, d.isin"""), {"since": str(since)}).fetchall()
    out = []
    for r in rows:
        m = dict(r._mapping)
        m["confirmed"] = m["bb_accepted_face_crore"] is not None
        m["diff_face_crore"] = (round(float(m["accepted_face_crore"]) - float(m["bb_accepted_face_crore"]), 2)
                                if m["confirmed"] else None)
        m["shown_source"] = "BB" if m["confirmed"] else "DESK (provisional)"
        out.append(m)
    return out


@router.get("/marks")
def get_marks(days: int = Query(45, ge=1, le=400)):
    """Every recent desk mark, with BB's published cut-off beside it.

    The comparison is the point: until BB publishes, the mark is all there is;
    once BB publishes, the difference says how good the desk's read was.
    """
    session = get_session()
    try:
        today = datetime.date.today()
        rows = session.execute(text("""
            SELECT d.auction_date, d.tenor_label, d.security_type, d.cutoff_yield_pct,
                   d.accepted_crore, d.entered_by, d.entered_utc, d.updated_utc, d.note,
                   p.cutoff_yield_pct AS bb_cutoff_yield_pct,
                   p.accepted_bdt_crore AS bb_accepted_crore,
                   p.issue_date AS bb_issue_date
            FROM desk_marks d
            LEFT JOIN primary_yield_snapshots p
                   ON p.auction_date = d.auction_date
                  AND p.tenor_label  = d.tenor_label
            WHERE d.auction_date >= :since
            ORDER BY d.auction_date DESC, d.tenor_label
        """), {"since": str(today - datetime.timedelta(days=days))}).fetchall()

        out = []
        for r in rows:
            m = dict(r._mapping)
            bb = m.get("bb_cutoff_yield_pct")
            m["confirmed"] = bb is not None
            # Basis points, signed: positive means the desk marked ABOVE what
            # BB printed.
            m["diff_bps"] = (round((float(m["cutoff_yield_pct"]) - float(bb)) * 100, 1)
                             if bb is not None else None)
            m["shown_cutoff_pct"] = float(bb) if bb is not None else float(m["cutoff_yield_pct"])
            m["shown_source"] = "BB" if bb is not None else "DESK (provisional)"
            out.append(m)

        unconfirmed = [m for m in out if not m["confirmed"]]
        worst = max((abs(m["diff_bps"]) for m in out if m["diff_bps"] is not None),
                    default=None)
        return {
            "as_of": str(today),
            "window_days": days,
            "count": len(out),
            "unconfirmed": len(unconfirmed),
            "worst_diff_bps": worst,
            "configured": bool(os.environ.get("DESK_WRITE_SECRET")),
            "tenors": sorted(_known_tenors(session), key=_tenor_order),
            "marks": out,
            "buybacks": desk_buybacks(session, today - datetime.timedelta(days=days)),
        }
    finally:
        session.close()
