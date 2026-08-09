"""
api/routers/policy.py — Bangladesh Bank policy-rate corridor.

Primary source is the live corridor the pipeline fetches daily from BB's
homepage POLICY RATES box (table policy_rate_snapshots) — authoritative and
self-updating. The effective-dated seed (seeds/policy_rates.yaml) is a
historical fallback used only until the DB has a fetched snapshot.
"""
from pathlib import Path
from fastapi import APIRouter
from sqlalchemy import text
from db import get_session

router = APIRouter()

_SEED = Path(__file__).resolve().parents[1] / "seeds" / "policy_rates.yaml"


def _load() -> list[dict]:
    """Seed corridor snapshots, ascending by effective_date. [] if missing."""
    try:
        import yaml
        with open(_SEED, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        rows = data.get("corridor", []) or []
        return sorted(rows, key=lambda r: str(r.get("effective_date", "")))
    except Exception:
        return []


def _db_current() -> dict | None:
    """Latest corridor fetched from BB (live). None if the table is empty."""
    try:
        s = get_session()
        try:
            row = s.execute(text(
                "SELECT first_seen_date, last_seen_date, repo, slf, sdf, bank_rate, "
                "crr, slr, source_last_update FROM policy_rate_snapshots "
                "ORDER BY first_seen_date DESC, id DESC LIMIT 1"
            )).fetchone()
        finally:
            s.close()
        if not row:
            return None
        m = row._mapping
        return {
            "effective_date": str(m["first_seen_date"]),
            "repo": m["repo"], "slf": m["slf"], "sdf": m["sdf"], "bank_rate": m["bank_rate"],
            "crr": m["crr"], "slr": m["slr"],
            "source": "BB homepage (live)",
            "verified": True,
            "last_checked": str(m["last_seen_date"]) if m["last_seen_date"] else None,
            "source_last_update": m["source_last_update"],
            "note": "Live from Bangladesh Bank — bb.org.bd POLICY RATES box",
        }
    except Exception:
        return None


@router.get("")
def get_policy_corridor():
    """
    { current: {effective_date, repo, slf, sdf, bank_rate, source, verified,
                last_checked, ...} | null,
      verified: bool,          # true when `current` came from the live fetch
      history: [ ...seed snapshots ascending... ] }
    """
    seed = _load()
    db_cur = _db_current()
    if db_cur:
        current = db_cur
    elif seed:
        current = {**seed[-1], "source": "seed (manual)", "verified": False}
    else:
        current = None
    return {"current": current, "verified": bool(db_cur), "history": seed}
