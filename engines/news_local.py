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
        if k.startswith(("LLM_", "LLM2_")) and k not in env:
            env[k] = v
            got[k] = v
    return got


LIFT_MAX_ARTICLES = 30


def lift_rule_reads(session, now=None, days: int = 3, max_articles: int = LIFT_MAX_ARTICLES) -> int:
    """Hand the articles the free rule reader read while this computer was off to the model.

    The workflow keeps reading in the background with rules when the PC is off; when it is on
    again the better reader takes a second look. A rule-read event is only a LEAD until then (never summed,
    never announced), so every article behind an unconfirmed lead is queued however old it is - bounded by
    COUNT, not age, because the daily call cap is a money cap - plus the rule-read articles of the last `days`.
    The model's reading confirms an agreeing lead and retires the rest (liquidity_news.supersede_rule_reads)."""
    import datetime
    import json

    from sqlalchemy import text

    from db import LiquidityEvent, NewsArticle
    from engines import news_rules
    now = now or datetime.datetime.utcnow()
    urls = []
    for ev in session.query(LiquidityEvent).filter(LiquidityEvent.verified_by == "rules",
                                                   LiquidityEvent.superseded_utc.is_(None)).all():
        urls += [x.get("url") for x in json.loads(ev.sources_json or "[]") if x.get("url")]
    recent = [u for (u,) in session.query(NewsArticle.url).filter(
        NewsArticle.triage_model == news_rules.RULES_MODEL, NewsArticle.body_text.isnot(None),
        NewsArticle.fetched_utc >= now - datetime.timedelta(days=days)).all()]
    todo = list(dict.fromkeys(urls + recent))[:max_articles]
    n = 0
    for a in session.query(NewsArticle).filter(NewsArticle.url.in_(todo)).all() if todo else []:
        if a.triage_status != "untriaged":
            a.triage_status = "untriaged"
            n += 1
    session.flush()
    return n


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_llm_settings()
    if not any(all(os.environ.get(f"{p}{k}") for k in ("BASE_URL", "API_KEY", "MODEL")) for p in ("LLM_", "LLM2_")):
        print("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL (and optionally LLM2_*) must be set in .env "
              "(see engines/news_local.py)")
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
