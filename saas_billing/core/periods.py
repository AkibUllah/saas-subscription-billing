"""Billing period arithmetic."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from enum import Enum


class Interval(str, Enum):
    MONTH = "month"
    YEAR = "year"

    @property
    def months(self) -> int:
        return 12 if self is Interval.YEAR else 1


def add_months(value: datetime, months: int) -> datetime:
    """Add calendar months, clamping the day to the target month's length (Jan 31 + 1 -> Feb 28)."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def next_period_end(start: datetime, interval: Interval, interval_count: int = 1) -> datetime:
    if interval_count < 1:
        raise ValueError("interval_count must be >= 1")
    return add_months(start, interval.months * interval_count)


def months_between(interval: Interval, interval_count: int = 1) -> int:
    return interval.months * interval_count


def seconds_between(start: datetime, end: datetime) -> int:
    delta: timedelta = end - start
    return int(delta.total_seconds())
