"""
seeds_loader.py — Load holiday YAML seed files into the database.

Run once per fiscal year when the BB holiday list is published.
Usage:  python seeds_loader.py --file data/seeds/holidays_2025-26.yaml
"""
import argparse
import datetime
import logging
import yaml
from db import init_db, get_session, HolidayCalendar

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def load_holiday_file(path: str) -> int:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    fy     = data.get("fiscal_year", "UNKNOWN")
    items  = data.get("holidays", [])
    session = get_session()
    count   = 0

    for item in items:
        d = datetime.date.fromisoformat(item["date"])
        existing = session.get(HolidayCalendar, d)
        if existing:
            existing.holiday_name = item.get("name", "")
            existing.holiday_type = item.get("type", "GOVT_GAZETTE")
        else:
            session.add(HolidayCalendar(
                calendar_date = d,
                holiday_name  = item.get("name", ""),
                holiday_type  = item.get("type", "GOVT_GAZETTE"),
                fiscal_year   = fy,
                source        = path,
            ))
        count += 1

    # The seed file is the source of truth for its fiscal year: a date removed
    # from the YAML must leave the DB too. Upsert-only loading is how the wrong
    # "approximate" Eid dates outlived their correction (Sep-2026 audit).
    file_dates = {datetime.date.fromisoformat(i["date"]) for i in items}
    stale = [r for r in session.query(HolidayCalendar).filter_by(fiscal_year=fy).all()
             if r.calendar_date not in file_dates]
    for r in stale:
        log.warning("Removing %s (%s) — no longer in %s", r.calendar_date, r.holiday_name, path)
        session.delete(r)

    session.commit()
    session.close()
    log.info("Loaded %d holidays for FY %s (%d stale removed)", count, fy, len(stale))
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    init_db()
    load_holiday_file(args.file)
