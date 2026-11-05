from datetime import datetime
import pytz


def get_utc_now() -> datetime:
    """Get current datetime in UTC timezone."""
    return datetime.now(pytz.UTC)


def convert_to_timezone(dt: datetime, tz_name: str) -> datetime:
    """Convert given UTC datetime to specific timezone."""
    try:
        target_tz = pytz.timezone(tz_name)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(target_tz)
    except pytz.UnknownTimeZoneError:
        return dt
