"""
backfill_callmoney.py — pull the full BB call-money history into call_money_rates.

WHY THIS EXISTS
The daily fetcher only ever asked BB for the last ~90 days, so the table began
in March 2026 — about five months. That single fact blocked the entire Tier-1
factor set in the forecasting work: call_spread (call money minus policy rate)
is the most theoretically promising driver of auction cutoffs, and it could not
be tested on ~22 auctions.

The limitation was in the CALLER, not the source. BB's call-money page accepts
an arbitrary date range and serves data back to at least 2021, which turns a
five-month series into a five-year one.

Resumable: skips (trade_date, product, maturity_days) rows already stored, so it
can be re-run after an interruption or to extend the range.

Usage:  python backfill_callmoney.py                 2021-01-01 -> today
        python backfill_callmoney.py 2023 2026       explicit year range
"""
import datetime
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from db import init_db, get_session, CallMoneyRate      # noqa: E402
from fetchers.callmoney import _parse_table, _URL       # noqa: E402

log = logging.getLogger("backfill_callmoney")

# BB's form returns a bounded page, so ask in quarters rather than one huge
# range — large windows silently truncate rather than erroring.
CHUNK_DAYS = 90
START_DEFAULT = datetime.date(2021, 1, 1)


def quarters(start: datetime.date, end: datetime.date):
    a = start
    while a <= end:
        b = min(a + datetime.timedelta(days=CHUNK_DAYS - 1), end)
        yield a, b
        a = b + datetime.timedelta(days=1)


def upsert(session, rows: list[dict]) -> int:
    """Insert rows not already present. Returns how many were new."""
    now = datetime.datetime.utcnow()
    saved = 0
    for r in rows:
        exists = session.query(CallMoneyRate).filter_by(
            trade_date=r["trade_date"], product=r["product"],
            maturity_days=r["maturity_days"]).first()
        if exists:
            continue
        r = dict(r)
        r["ingested_utc"] = now
        session.add(CallMoneyRate(**r))
        saved += 1
    session.commit()
    return saved


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
    args = [a for a in sys.argv[1:] if a.isdigit()]
    start = datetime.date(int(args[0]), 1, 1) if args else START_DEFAULT
    end = datetime.date(int(args[1]), 12, 31) if len(args) > 1 else datetime.date.today()
    end = min(end, datetime.date.today())

    init_db()
    from fetchers.bb_session import get_f5_cookies, bb_post
    # One F5 session is reused for every chunk. Re-solving the challenge per
    # request would be slow and looks far more like scraping than a person
    # paging through the form.
    cookies, ua = get_f5_cookies(_URL, wait_selector="input[name='date_picker']")
    log.info("BB session established; backfilling %s -> %s", start, end)

    session = get_session()
    total_new = total_seen = 0
    try:
        for a, b in quarters(start, end):
            rng = a.strftime("%d/%m/%Y") + " - " + b.strftime("%d/%m/%Y")
            try:
                html = bb_post(_URL, {"date_picker": rng}, cookies, ua)
                rows = _parse_table(html) if html else []
            except Exception as exc:
                log.warning("%s..%s failed: %s", a, b, exc)
                continue
            new = upsert(session, rows)
            total_new += new
            total_seen += len(rows)
            log.info("%s..%s  parsed=%-5d new=%-5d", a, b, len(rows), new)
        n = session.query(CallMoneyRate).count()
        first = session.query(CallMoneyRate).order_by(CallMoneyRate.trade_date).first()
        last = session.query(CallMoneyRate).order_by(CallMoneyRate.trade_date.desc()).first()
        log.info("done: parsed %d rows, %d new. Table now %d rows, %s -> %s",
                 total_seen, total_new, n,
                 first.trade_date if first else None, last.trade_date if last else None)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
