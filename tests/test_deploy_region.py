"""The database is in Singapore (Supabase ap-southeast-1). A Vercel function defaults to Washington, and every
database round trip from there crosses the world: with connection setup, one cheap query took ~1 s and the
pages that run several took 3-5 s. Both projects are pinned next to the database; losing the pin brings the
slowness back with no error anywhere, so it is a test."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_api_and_site_run_in_the_databases_region():
    for rel in ("api/vercel.json", "frontend/vercel.json"):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            assert json.load(fh).get("regions") == ["sin1"], f"{rel} must pin the functions to Singapore"
