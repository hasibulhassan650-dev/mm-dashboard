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
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from db import get_session

router = APIRouter()

# Tenors BB actually auctions. A free-text label would let a typo create a
# brand-new tenor that then appears on the curve as a real point.
_TENORS = {"91D", "182D", "364D", "2Y", "5Y", "10Y", "15Y", "20Y",
           "3Y_FRTB", "5Y_FRTB", "10Y_FRTB"}


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
        if v not in _TENORS:
            raise ValueError(f"unknown tenor '{v}' — one of {sorted(_TENORS)}")
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
            "tenors": sorted(_TENORS),
            "marks": out,
        }
    finally:
        session.close()
