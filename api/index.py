"""
api/index.py — Vercel serverless entrypoint.

Vercel's @vercel/python builder serves the ASGI `app` exported here. All
routes are rewritten to this single function (see root vercel.json), and
FastAPI does its own internal routing from the full path. This lets the
existing app run unchanged on Vercel's free tier — no Chrome, just reads
from Supabase.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app  # noqa: E402,F401  (re-exported for the Vercel builder)
