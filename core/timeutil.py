"""
Time helpers — the single source of "what time is it for the owner".

Every manager used to call datetime.now()/date.today() and read the
server's clock, which on Render (and most hosts) is UTC. VOID lives in
settings.tz (Asia/Kolkata), so Emma thought it was 5h30m earlier than it
really was: she scheduled tasks already past, told him to do them, and let
reminders fire hours late.

All modules must use local_now()/local_today() instead of bare now()/today().
These return naive datetimes/dates in the owner's zone (naive on purpose —
the block start/end times in the DB are stored as naive local wall-clock).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import get_settings

_zone_cache: dict[str, ZoneInfo] = {}


def _zone() -> ZoneInfo:
    tz_name = get_settings().tz or "UTC"
    if tz_name not in _zone_cache:
        _zone_cache[tz_name] = ZoneInfo(tz_name)
    return _zone_cache[tz_name]


def local_now() -> datetime:
    """Naive local wall-clock time in settings.tz (e.g. Asia/Kolkata)."""
    return datetime.now(_zone()).replace(tzinfo=None)


def local_today() -> date:
    """Naive local date in settings.tz."""
    return local_now().date()


def local_tz() -> ZoneInfo:
    """The settings timezone, for converting aware datetimes to local."""
    return _zone()


def tz_name() -> str:
    return get_settings().tz or "UTC"