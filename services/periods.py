"""Reusable reporting periods. One implementation for every page.

Periods are calendar days in America/New_York (configurable), converted to
UTC half-open ranges [start, end) for database filtering. DST is handled by
zoneinfo, never by subtracting fixed hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config import get_settings

PRESETS = [
    "Today", "Yesterday", "Tomorrow", "This Week", "Last Week", "Biweekly",
    "This Month", "Last Month", "Next 7 Days", "Remaining This Week", "Custom",
]


@dataclass(frozen=True)
class Period:
    label: str
    start_date: date  # local, inclusive
    end_date: date  # local, inclusive
    tz: ZoneInfo

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start_date, time.min, self.tz).astimezone(timezone.utc)

    @property
    def end_utc(self) -> datetime:
        """Exclusive upper bound."""
        return datetime.combine(self.end_date + timedelta(days=1), time.min, self.tz).astimezone(timezone.utc)

    def contains(self, ts: datetime | None) -> bool:
        return ts is not None and self.start_utc <= ts < self.end_utc


def _week_start(d: date, starts_on: str) -> date:
    # Python: Monday=0 .. Sunday=6
    offset = (d.weekday() + 1) % 7 if starts_on.upper() == "SUNDAY" else d.weekday()
    return d - timedelta(days=offset)


def local_today(tz: ZoneInfo | None = None, now: datetime | None = None) -> date:
    tz = tz or get_settings().tz
    return (now or datetime.now(timezone.utc)).astimezone(tz).date()


def period(preset: str, now: datetime | None = None, custom: tuple[date, date] | None = None,
           tz: ZoneInfo | None = None, week_starts_on: str | None = None) -> Period:
    s = get_settings()
    tz = tz or s.tz
    starts_on = week_starts_on or s.week_starts_on
    today = local_today(tz, now)
    ws = _week_start(today, starts_on)
    first_of_month = today.replace(day=1)
    if preset == "Today":
        a, b = today, today
    elif preset == "Yesterday":
        a = b = today - timedelta(days=1)
    elif preset == "Tomorrow":
        a = b = today + timedelta(days=1)
    elif preset == "This Week":
        a, b = ws, ws + timedelta(days=6)
    elif preset == "Last Week":
        a, b = ws - timedelta(days=7), ws - timedelta(days=1)
    elif preset == "Biweekly":  # current + previous week
        a, b = ws - timedelta(days=7), ws + timedelta(days=6)
    elif preset == "This Month":
        nxt = (first_of_month + timedelta(days=32)).replace(day=1)
        a, b = first_of_month, nxt - timedelta(days=1)
    elif preset == "Last Month":
        b = first_of_month - timedelta(days=1)
        a = b.replace(day=1)
    elif preset == "Next 7 Days":
        a, b = today, today + timedelta(days=6)
    elif preset == "Remaining This Week":
        a, b = today, ws + timedelta(days=6)
    elif preset == "Custom":
        if not custom:
            raise ValueError("Custom period needs (start_date, end_date)")
        a, b = custom
    else:
        raise ValueError(f"Unknown period {preset}")
    if b < a:
        a, b = b, a
    return Period(preset, a, b, tz)


def to_local(ts: datetime | None, tz: ZoneInfo | None = None) -> datetime | None:
    return ts.astimezone(tz or get_settings().tz) if ts else None
