"""When is a daily series 'current'?  One definition, shared by the status endpoint (and so by the sentinel and the site banner).

Bangladesh Bank publishes call money, reference rates, GSOM and the cash-flow ladder for the day itself, but the interbank FX, the exchange
rates and the interbank repo pages describe the PREVIOUS business day and are posted on the next one. Measured from first-ingest history:
Wed 18-Nov is first stored Thu 19-Nov; Thu 12-Nov is first stored Sun 15-Nov (Friday and Saturday carry nothing). So on the Friday after a
Thursday, the Thursday figure CANNOT exist yet and "behind the last working day" there is BB's cadence, not a fault. Judging it so opened a
fresh uptime alert every weekend and every morning.
"""
import datetime

# Datasets whose pages are posted one working day after the day they describe (see module docstring).
PUBLISHED_NEXT_WORKING_DAY = frozenset({"fxmarket", "fxrates", "repo"})


def previous_working_day(day: datetime.date) -> datetime.date:
    """Most recent Bangladesh working day strictly before `day` (weekend = Friday and Saturday)."""
    d = day - datetime.timedelta(days=1)
    while d.weekday() in (4, 5):
        d -= datetime.timedelta(days=1)
    return d


def required_date(key: str, today: datetime.date) -> datetime.date:
    """The newest data date a daily series must hold to count as current today."""
    lwd = previous_working_day(today)
    return previous_working_day(lwd) if key in PUBLISHED_NEXT_WORKING_DAY else lwd
