"""
forecast/policy.py — the policy corridor as a modelling input.

WHY THIS FILE IS MOSTLY A GUARD
The error-correction model in the project guide rests on one idea: auction
cutoffs are tied to BB's policy rate and get pulled back toward it. Testing that
needs the corridor as a STEP FUNCTION THROUGH TIME. `policy_rate_snapshots` only
ever holds today's corridor, and the effective-dated seed currently contains
three entries, two of which say in their own note that they were drafted from
memory.

Fitting an ECM on that would not fail loudly — it would produce a confident
alpha and a plausible-looking pull-to-anchor built on invented numbers, which is
strictly worse than having no model. So everything here is built and wired, and
`usable()` refuses to let any of it run until real verified history exists.

Unlock path:
  1. Fill api/seeds/policy_rates.yaml with every corridor change back to at
     least the start of the fit window, each checked against a BB circular.
  2. Set `verified: true` on each entry you actually checked.
  3. python forecast/policy.py --load
The ECM and the cointegration test switch themselves on.

Usage:  python forecast/policy.py --load     load seed -> policy_rate_history
        python forecast/policy.py --status   print the gate's verdict
"""
import datetime
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import init_db, get_session, PolicyRateHistory      # noqa: E402

log = logging.getLogger("policy")

SEED = ROOT / "api" / "seeds" / "policy_rates.yaml"

# The gate. An ECM needs the anchor to actually vary across the fit window —
# a corridor that never moves cannot explain anything, and one that starts
# after the auctions do leaves most rows with no anchor at all.
MIN_VERIFIED_POINTS = 4     # distinct verified corridor observations
MIN_DISTINCT_REPO   = 3     # repo must actually take several levels
MAX_START_LAG_DAYS  = 31    # history must begin at/before the fit window start


def load_seed(path: Path = SEED) -> list[dict]:
    """Read the effective-dated corridor seed. Missing/!parseable -> []."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        rows = data.get("corridor", []) or []
        return sorted(rows, key=lambda r: str(r.get("effective_date", "")))
    except Exception as exc:
        log.warning("could not read policy seed %s: %s", path, exc)
        return []


def load_to_db(session, rows: list[dict]) -> int:
    """Upsert seed entries into policy_rate_history on effective_date."""
    now = datetime.datetime.utcnow()
    n = 0
    for r in rows:
        eff = r.get("effective_date")
        if not eff:
            continue
        eff = datetime.date.fromisoformat(str(eff))
        existing = session.query(PolicyRateHistory).filter_by(effective_date=eff).first()
        payload = dict(
            repo=r.get("repo"), slf=r.get("slf"), sdf=r.get("sdf"),
            bank_rate=r.get("bank_rate"),
            # Absent flag means unverified. Never default this to True.
            verified=bool(r.get("verified", False)),
            source=str(r.get("source", "api/seeds/policy_rates.yaml"))[:300],
            note=r.get("note"), ingested_utc=now,
        )
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            session.add(PolicyRateHistory(effective_date=eff, **payload))
            n += 1
    session.commit()
    return n


def load_history(session) -> pd.DataFrame:
    """Verified corridor history only, ascending. Unverified rows are dropped
    here rather than filtered downstream, so no caller can accidentally use them."""
    rows = (session.query(PolicyRateHistory.effective_date, PolicyRateHistory.repo,
                          PolicyRateHistory.slf, PolicyRateHistory.sdf,
                          PolicyRateHistory.verified)
            .order_by(PolicyRateHistory.effective_date).all())
    df = pd.DataFrame(rows, columns=["effective_date", "repo", "slf", "sdf", "verified"])
    if df.empty:
        return df
    df = df[df["verified"].fillna(False).astype(bool)].copy()
    if df.empty:
        return df
    df["effective_date"] = pd.to_datetime(df["effective_date"])
    return df.reset_index(drop=True)


def usable(hist: pd.DataFrame, window_start) -> tuple[bool, str]:
    """Can the corridor carry an ECM? Returns (ok, human-readable reason).

    The reason string is surfaced verbatim in the UI — someone looking at a
    blocked ECM should be told exactly which condition failed and by how much.
    """
    if hist.empty:
        return False, "no verified corridor history — every seed entry is unverified"
    n = len(hist)
    if n < MIN_VERIFIED_POINTS:
        return False, (f"only {n} verified corridor change(s); need "
                       f"{MIN_VERIFIED_POINTS} to fit a pull-to-anchor")
    distinct = hist["repo"].nunique(dropna=True)
    if distinct < MIN_DISTINCT_REPO:
        return False, (f"repo takes only {distinct} distinct value(s) in verified "
                       f"history; an anchor that never moves explains nothing")
    start = hist["effective_date"].min()
    ws = pd.Timestamp(window_start)
    lag = (start - ws).days
    if lag > MAX_START_LAG_DAYS:
        return False, (f"verified history starts {start.date()}, "
                       f"{lag} days after the fit window opens {ws.date()}")
    return True, f"{n} verified corridor changes from {start.date()}"


def attach(f: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    """Attach the corridor in force at each auction, and its move since the last.

    merge_asof backward with exact matches ALLOWED: a corridor effective on the
    auction date is public that morning and is genuinely in the bidder's
    information set — unlike another auction's result.
    """
    out = f.copy()
    if hist.empty:
        out["repo_now"] = np.nan
        out["spread_policy_lag1"] = np.nan
        out["d_policy"] = np.nan
        return out

    merged = pd.merge_asof(
        out.sort_values("auction_date"),
        hist[["effective_date", "repo"]].sort_values("effective_date"),
        left_on="auction_date", right_on="effective_date",
        direction="backward", allow_exact_matches=True,
    ).rename(columns={"repo": "repo_now"})
    merged = merged.drop(columns=["effective_date"], errors="ignore")

    # The error-correction term: how far the PREVIOUS cutoff sat above the
    # corridor. If cutoffs are cointegrated with the policy rate, a wide spread
    # should predict a fall back toward it (alpha < 0).
    merged["spread_policy_lag1"] = merged["cutoff_lag1"] - merged["repo_now"]
    # Policy moves between auctions are the step-changes a rate model must see.
    merged["d_policy"] = merged["repo_now"].diff()
    return merged


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL is not set")
        return 1
    init_db()
    session = get_session()
    try:
        if "--load" in sys.argv:
            rows = load_seed()
            n = load_to_db(session, rows)
            log.info("seed had %d entries (%d verified); %d newly inserted",
                     len(rows), sum(1 for r in rows if r.get("verified")), n)
        hist = load_history(session)
        window_start = datetime.date.today() - datetime.timedelta(days=365 * 3)
        ok, why = usable(hist, window_start)
        log.info("ECM gate: %s — %s", "OPEN" if ok else "BLOCKED", why)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
