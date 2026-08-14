from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import get_settings


def app_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now_local() -> datetime:
    return datetime.now(app_tz())


def shift_date_for(moment: datetime | None = None) -> date:
    """Shift belonging to calendar day D is open from 05:00 D until 05:00 D+1."""
    settings = get_settings()
    moment = moment or now_local()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=app_tz())
    local = moment.astimezone(app_tz())
    if local.hour < settings.shift_open_hour:
        return (local - timedelta(days=1)).date()
    return local.date()


def shift_window(shift_date: date) -> tuple[datetime, datetime]:
    settings = get_settings()
    tz = app_tz()
    opened = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        settings.shift_open_hour,
        0,
        0,
        tzinfo=tz,
    )
    closed = opened + timedelta(days=1)
    return opened, closed


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=app_tz())
    return dt.astimezone(timezone.utc)
