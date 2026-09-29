"""api/db.py — DB connection for the API (read-only, Supabase)."""
import importlib.util
import logging
import os
from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)


def _normalise_pg_driver(url: str) -> str:
    """Name the Postgres driver explicitly, and only one that is installed.

    Same guard as config.py, kept deliberately duplicated: this module is the
    API's only DB bootstrap and must not import the repo-root config. A bare
    'postgresql://' URL lets the library pick the driver, and SQLAlchemy 2.1
    changed that default from psycopg2 to psycopg v3 — which took every cloud
    data job down for five days in Sep-2026. api/requirements.txt pins 2.0.36
    today; this makes the API survive the day that pin moves.
    """
    if "://" not in url or not url.startswith("postgres"):
        return url
    scheme, rest = url.split("://", 1)
    named = scheme.split("+", 1)[1] if "+" in scheme else None
    have = [d for d in ("psycopg2", "psycopg") if importlib.util.find_spec(d) is not None]
    if not have or named in have:
        return url
    if named:
        log.warning("DATABASE_URL names the '%s' driver, which is not installed — using '%s'.",
                    named, have[0])
    return f"postgresql+{have[0]}://{rest}"


@lru_cache(maxsize=1)
def _engine():
    url = _normalise_pg_driver(os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1))
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def get_session():
    Session = sessionmaker(bind=_engine())
    return Session()
