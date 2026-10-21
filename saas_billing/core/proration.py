"""Proration: how much of a period's price is still unused at a point in time.

Proration is computed on exact elapsed seconds (as a `Fraction`), then rounded to minor units
once, so credits and charges for the same remaining window never disagree by a paisa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from saas_billing.core.money import scale


def remaining_fraction(period_start: datetime, period_end: datetime, at: datetime) -> Fraction:
    """Fraction of the period that lies after `at`, clamped to [0, 1]."""
    total = int((period_end - period_start).total_seconds())
    if total <= 0:
        raise ValueError("period_end must be after period_start")
    remaining = int((period_end - at).total_seconds())
    remaining = max(0, min(total, remaining))
    return Fraction(remaining, total)


def prorate(amount_minor: int, period_start: datetime, period_end: datetime, at: datetime) -> int:
    """Amount attributable to the unused part of the period after `at`."""
    return scale(amount_minor, remaining_fraction(period_start, period_end, at))


@dataclass(frozen=True)
class Proration:
    """Result of switching from one recurring price to another mid-period."""

    credit_minor: int  # unused part of what was already paid (>= 0)
    charge_minor: int  # remaining part of the new price (>= 0)
    fraction: Fraction

    @property
    def net_minor(self) -> int:
        return self.charge_minor - self.credit_minor


def prorate_change(
    old_amount_minor: int,
    new_amount_minor: int,
    period_start: datetime,
    period_end: datetime,
    at: datetime,
) -> Proration:
    fraction = remaining_fraction(period_start, period_end, at)
    return Proration(
        credit_minor=scale(old_amount_minor, fraction),
        charge_minor=scale(new_amount_minor, fraction),
        fraction=fraction,
    )
