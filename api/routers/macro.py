"""
api/routers/macro.py — Macro indicators (FX reserves & remittances).

Source of truth is api/seeds/reserves_remittances.yaml (monthly series), read
directly — same seed-backed pattern as the policy corridor. Edit the seed +
redeploy to update. Lives inside api/ so it deploys with the Railway backend.
"""
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

_SEED = Path(__file__).resolve().parents[1] / "seeds" / "reserves_remittances.yaml"


def _load() -> list[dict]:
    """Monthly rows sorted ascending by month. [] if missing/unreadable."""
    try:
        import yaml
        with open(_SEED, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        rows = data.get("series", []) or []
        return sorted(rows, key=lambda r: str(r.get("month", "")))
    except Exception:
        return []


@router.get("/reserves")
def get_reserves_remittances():
    """
    FX reserves & remittances monthly series.
      { series: [ ...ascending... ], latest: {...} | null }
    """
    rows = _load()
    return {"series": rows, "latest": rows[-1] if rows else None}
