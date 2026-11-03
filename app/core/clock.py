"""All product dates are Indian Standard Time (UTC+5:30, no DST). A 'day' rolls over at 00:00 IST."""
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_ist() -> str:
    return now_utc().astimezone(IST).date().isoformat()


def yesterday_ist() -> str:
    return (now_utc().astimezone(IST).date() - timedelta(days=1)).isoformat()


def shift_date(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def days_between(a: str, b: str) -> int:
    """b - a in whole days."""
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
