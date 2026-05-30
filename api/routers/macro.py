"""
api/routers/macro.py — Macro indicators (FX reserves & remittances).

Source of truth is api/seeds/reserves_remittances.yaml (monthly series), read
directly — same seed-backed pattern as the policy corridor. Edit the seed +
redeploy to update. Lives inside api/ so it deploys with the Railway backend.
"""
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()

_SEEDS = Path(__file__).resolve().parents[1] / "seeds"
_RESERVES = _SEEDS / "reserves_remittances.yaml"
_MONETARY = _SEEDS / "monetary.yaml"


def _read_yaml(path) -> dict:
    """Parse a seed YAML to a dict. {} if missing/unreadable."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


@router.get("/reserves")
def get_reserves_remittances():
    """
    FX reserves & remittances monthly series.
      { series: [ ...ascending... ], latest: {...} | null }
    """
    rows = sorted(_read_yaml(_RESERVES).get("series", []) or [],
                  key=lambda r: str(r.get("month", "")))
    return {"series": rows, "latest": rows[-1] if rows else None}


@router.get("/monetary")
def get_monetary():
    """
    Monetary & prices: CPI inflation, monetary aggregates, deposit/lending
    rates (monthly), plus effective-dated CRR/SLR requirements.
      { monthly: [...asc...], latest: {...}|null,
        reserve_requirements: { current: {...}|null, history: [...asc...] } }
    """
    data = _read_yaml(_MONETARY)
    monthly = sorted(data.get("monthly", []) or [], key=lambda r: str(r.get("month", "")))
    rr = sorted(data.get("reserve_requirements", []) or [],
                key=lambda r: str(r.get("effective_date", "")))
    return {
        "monthly": monthly,
        "latest": monthly[-1] if monthly else None,
        "reserve_requirements": {"current": rr[-1] if rr else None, "history": rr},
    }
