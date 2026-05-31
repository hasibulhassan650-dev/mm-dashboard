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


def _db_remittance() -> dict:
    """Real fetched remittance per month from the DB, keyed by 'YYYY-MM'. {} on any error."""
    out: dict = {}
    try:
        from sqlalchemy import text
        from db import get_session
        session = get_session()
        try:
            rows = session.execute(text(
                "SELECT month, remittance_usd_mn, remittance_bdt_bn FROM remittance_monthly"
            )).fetchall()
            for r in rows:
                m = r._mapping
                out[m["month"]] = m["remittance_usd_mn"]
        finally:
            session.close()
    except Exception:
        pass
    return out


@router.get("/reserves")
def get_reserves_remittances():
    """
    FX reserves (seed) + real fetched remittances (DB), merged by month.
      { series: [ ...ascending... ], latest: {...} | null }
    """
    seed = {r["month"]: r for r in (_read_yaml(_RESERVES).get("series", []) or []) if r.get("month")}
    rem = _db_remittance()
    months = sorted(set(seed) | set(rem))
    series = []
    for mo in months:
        s = seed.get(mo, {})
        series.append({
            "month": mo,
            "gross_reserves_usd_bn": s.get("gross_reserves_usd_bn"),
            "net_reserves_bpm6_usd_bn": s.get("net_reserves_bpm6_usd_bn"),
            # prefer real fetched remittance; fall back to any seed value
            "remittance_usd_mn": rem.get(mo, s.get("remittance_usd_mn")),
            "note": s.get("note"),
        })
    return {"series": series, "latest": series[-1] if series else None}


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
