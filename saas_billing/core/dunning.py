"""Dunning: when to retry a failed charge, and when to give up."""

from __future__ import annotations

from datetime import datetime, timedelta

DEFAULT_SCHEDULE_DAYS: tuple[int, ...] = (1, 3, 5, 7)


def parse_schedule(raw: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not values:
        raise ValueError("dunning schedule must contain at least one value")
    if any(v <= 0 for v in values):
        raise ValueError("dunning schedule values must be positive days")
    if list(values) != sorted(values):
        raise ValueError("dunning schedule must be increasing")
    return values


def next_retry_at(
    issued_at: datetime, failed_attempts: int, schedule: tuple[int, ...] = DEFAULT_SCHEDULE_DAYS
) -> datetime | None:
    """Retry time after `failed_attempts` failures, or None when the schedule is exhausted.

    The first charge happens at issue time; retry *n* happens `schedule[n-1]` days after issue.
    """
    if failed_attempts < 1:
        raise ValueError("failed_attempts must be >= 1")
    index = failed_attempts - 1
    if index >= len(schedule):
        return None
    return issued_at + timedelta(days=schedule[index])


def max_attempts(schedule: tuple[int, ...] = DEFAULT_SCHEDULE_DAYS) -> int:
    return len(schedule) + 1
