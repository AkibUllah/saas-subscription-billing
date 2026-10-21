"""Coupon arithmetic and duration semantics."""

from __future__ import annotations

import math
from enum import Enum

from saas_billing.core.money import percent_of


class CouponKind(str, Enum):
    PERCENT = "percent"
    FIXED = "fixed"


class CouponDuration(str, Enum):
    ONCE = "once"
    REPEATING = "repeating"
    FOREVER = "forever"


def discount_amount(
    subtotal_minor: int, kind: CouponKind, percent_bps: int | None, amount_minor: int | None
) -> int:
    """Discount for a subtotal; never more than the subtotal and never negative."""
    if subtotal_minor <= 0:
        return 0
    if kind is CouponKind.PERCENT:
        if percent_bps is None:
            raise ValueError("percent coupon needs percent_bps")
        value = percent_of(subtotal_minor, percent_bps)
    else:
        if amount_minor is None:
            raise ValueError("fixed coupon needs amount_minor")
        value = amount_minor
    return max(0, min(subtotal_minor, value))


def periods_covered(
    duration: CouponDuration, duration_in_months: int | None, months_per_period: int
) -> int | None:
    """How many invoices the coupon applies to; None means forever."""
    if duration is CouponDuration.FOREVER:
        return None
    if duration is CouponDuration.ONCE:
        return 1
    if not duration_in_months or duration_in_months <= 0:
        raise ValueError("repeating coupon needs duration_in_months")
    return max(1, math.ceil(duration_in_months / months_per_period))


def still_applies(
    duration: CouponDuration,
    duration_in_months: int | None,
    months_per_period: int,
    applications_so_far: int,
) -> bool:
    covered = periods_covered(duration, duration_in_months, months_per_period)
    return covered is None or applications_so_far < covered
