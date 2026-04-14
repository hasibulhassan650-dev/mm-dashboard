import sys, os, pathlib, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import init_db
from seeds_loader import load_holiday_file
from engines.pipeline import run_pipeline

init_db()

seed = pathlib.Path("data/seeds/holidays_2025-26.yaml")
if seed.exists():
    load_holiday_file(str(seed))

run_pipeline(
    datetime.date.today() - datetime.timedelta(days=60),
    datetime.date.today() + datetime.timedelta(days=120),
)

from dashboard.app import *