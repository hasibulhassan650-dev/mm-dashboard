"""
run.py — CLI entry point for the MM Dashboard pipeline.

Usage:
  python run.py init                  # Initialise database
  python run.py seed                  # Load holiday seeds
  python run.py pipeline              # Run full ETL
  python run.py pipeline --days 90    # ETL for next 90 days
  python run.py dashboard             # Launch Streamlit (calls streamlit run)
"""
import argparse
import datetime
import logging
import sys
import os
import subprocess

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/mm_dashboard.log", mode="a"),
    ]
)
log = logging.getLogger("run")


def cmd_init(args):
    from db import init_db
    init_db()
    log.info("Database initialised.")


def cmd_seed(args):
    from db import init_db
    from seeds_loader import load_holiday_file
    from config import SEEDS_DIR
    init_db()
    seed_files = list(SEEDS_DIR.glob("holidays_*.yaml"))
    if not seed_files:
        log.warning("No holiday YAML files found in %s", SEEDS_DIR)
        return
    for f in seed_files:
        count = load_holiday_file(str(f))
        log.info("Seeded %d holidays from %s", count, f.name)


def cmd_pipeline(args):
    from engines.pipeline import run_pipeline
    days = getattr(args, "days", 90)
    date_from = datetime.date.today() - datetime.timedelta(days=60)
    date_to   = datetime.date.today() + datetime.timedelta(days=days)
    log.info("Running pipeline: %s to %s", date_from, date_to)
    summary = run_pipeline(date_from, date_to)
    log.info("Pipeline result: %s", summary)
    if summary["errors"]:
        log.error("Errors: %s", summary["errors"])
        sys.exit(1)


def cmd_dashboard(args):
    app_path = os.path.join(os.path.dirname(__file__), "dashboard", "app.py")
    subprocess.run(["streamlit", "run", app_path], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BD Money Market Dashboard")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init",      help="Initialise database")
    subparsers.add_parser("seed",      help="Load holiday seed files")
    p_pipe = subparsers.add_parser("pipeline", help="Run ETL pipeline")
    p_pipe.add_argument("--days", type=int, default=90,
                        help="Number of future days to compute (default: 90)")
    subparsers.add_parser("dashboard", help="Launch Streamlit dashboard")

    args = parser.parse_args()
    os.makedirs("logs", exist_ok=True)

    {
        "init":      cmd_init,
        "seed":      cmd_seed,
        "pipeline":  cmd_pipeline,
        "dashboard": cmd_dashboard,
    }.get(args.command, lambda _: parser.print_help())(args)
