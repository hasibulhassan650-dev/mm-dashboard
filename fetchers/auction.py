"""
fetchers/auction.py — Thin wrapper that delegates to fetchers/auction_parser.py.
This file exists for backward compatibility with pipeline.py imports.
"""
from fetchers.auction_parser import fetch_auction_rows   # noqa: F401 (re-export)
