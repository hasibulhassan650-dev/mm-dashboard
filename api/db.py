"""api/db.py — DB connection for the API (read-only, Supabase)."""
import os
from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@lru_cache(maxsize=1)
def _engine():
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)

def get_session():
    Session = sessionmaker(bind=_engine())
    return Session()
