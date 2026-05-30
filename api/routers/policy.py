"""
api/routers/policy.py — Bangladesh Bank policy-rate corridor.

Source of truth is data/seeds/policy_rates.yaml (effective-dated full-corridor
snapshots), read directly — no DB table, since these change only a few times a
year via MPC announcements. Edit the seed + redeploy to update.
"""
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

# Seed lives inside api/ so it deploys with the backend (Railway root = api/).
# This file is api/routers/policy.py → parents[1] == api/.
_SEED = Path(__file__).resolve().parents[1] / "seeds" / "policy_rates.yaml"


def _load() -> list[dict]:
    """Read corridor snapshots, sorted ascending by effective_date. [] if missing."""
    try:
        import yaml
        with open(_SEED, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        rows = data.get("corridor", []) or []
        return sorted(rows, key=lambda r: str(r.get("effective_date", "")))
    except Exception:
        return []


@router.get("")
def get_policy_corridor():
    """
    Returns the current corridor (latest snapshot) + full history.
      { current: {effective_date, repo, slf, sdf, bank_rate, note} | null,
        history: [ ...snapshots ascending... ] }
    """
    rows = _load()
    return {
        "current": rows[-1] if rows else None,
        "history": rows,
    }
