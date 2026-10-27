"""Run the Liquidity Watch on THIS computer, with a model that only this computer can reach.

A local proxy such as http://127.0.0.1:8317 is not reachable from GitHub's runners (127.0.0.1
there is the runner itself), so the scheduled workflow cannot use it. This entry point does the
same pass from your own machine: it reads LLM_BASE_URL / LLM_API_KEY / LLM_MODEL from the
git-ignored .env file (never from the repository), then runs exactly what the workflow runs.

    python -m engines.news_local            # one pass
    python -m engines.news_local announce   # also open issues for events that matter

It writes to the same database, so events it finds appear on /liquidity-watch and are announced
by the workflow's next announce step. The workflow itself keeps running with the free rule reader
whenever this computer is off.
"""
import os
import sys

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def load_llm_settings(path: str = _ENV, env=None) -> dict:
    """LLM_* lines from the .env file; never overrides what is already set, never reads other keys."""
    env = os.environ if env is None else env
    got = {}
    if not os.path.exists(path):
        return got
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k.startswith("LLM_") and k not in env:
            env[k] = v
            got[k] = v
    return got


def lift_rule_reads(session, now=None, days: int = 3) -> int:
    """Hand the articles the free rule reader read while this computer was off to the model.

    The workflow keeps reading in the background with rules when the PC is off; when it is on
    again the better reader takes a second look at the recent ones. Events the rules already
    found merge with what the model reports (same kind, counterparty and amount), so nothing is
    counted twice. Bounded to `days`, because the daily call cap is a money cap."""
    import datetime

    from sqlalchemy import text

    from engines import news_rules
    now = now or datetime.datetime.utcnow()
    return session.execute(text(
        "UPDATE news_articles SET triage_status = 'untriaged' WHERE triage_model = :m "
        "AND body_text IS NOT NULL AND fetched_utc >= :s"),
        {"m": news_rules.RULES_MODEL, "s": now - datetime.timedelta(days=days)}).rowcount


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_llm_settings()
    if not all(os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")):
        print("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set in .env (see engines/news_local.py)")
        return 2
    from db import get_session, init_db
    init_db()
    s = get_session()
    try:
        n = lift_rule_reads(s)
        s.commit()
        if n:
            print(f"{n} article(s) read by the rule reader while this computer was off will be re-read by the model")
    finally:
        s.close()
    from engines import news_triage
    rc = news_triage.main(["run"])
    if "announce" in argv:
        from engines import announce
        rc = max(rc, announce.main(["publish"]))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
