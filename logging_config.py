"""logging_config.py — Shared logging setup for all modules."""
import logging
import sys
from config import LOGS_DIR

def setup_logging(level=logging.INFO):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "mm_dashboard.log", mode="a"),
    ]
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
