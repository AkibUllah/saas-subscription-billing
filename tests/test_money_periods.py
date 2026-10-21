from datetime import datetime, timezone
from fractions import Fraction

import pytest

from saas_billing.core.money import format_money, percent_of, round_half_up, scale
from saas_billing.core.periods import Interval, add_months, next_period_end
from saas_billing.core.proration import prorate, prorate_change, remaining_fraction

UTC = timezone.utc


def test_percent_of_rounds_half_up():
    assert percent_of(100_000, 1500) == 15_000
    assert percent_of(333, 1500) == 50  # 49.95 -> 50
    assert percent_of(1, 5000) == 1  # 0.5 -> 1
    with pytest.raises(ValueError):
        percent_of(100, -1)


def test_scale_and_round_half_up():
    assert scale(1_000, Fraction(1, 3)) == 333
    assert scale(1_000, Fraction(2, 3)) == 667
    assert round_half_up(Fraction(5, 2)) == 3


def test_format_money_bdt_and_negative():
    assert format_money(150_000) == "৳1,500.00"
    assert format_money(-46_370, "BDT") == "-৳463.70"
    assert format_money(1_999, "USD") == "$19.99"
    assert format_money(5, "XYZ") == "XYZ 0.05"


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (datetime(2026, 1, 31, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
        (datetime(2028, 1, 31, tzinfo=UTC), 1, datetime(2028, 2, 29, tzinfo=UTC)),
        (datetime(2026, 11, 15, tzinfo=UTC), 2, datetime(2027, 1, 15, tzinfo=UTC)),
        (datetime(2026, 3, 31, tzinfo=UTC), 12, datetime(2027, 3, 31, tzinfo=UTC)),
    ],
)
def test_add_months_clamps_day(start, months, expected):
    assert add_months(start, months) == expected


def test_next_period_end_by_interval():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert next_period_end(start, Interval.MONTH) == datetime(2026, 2, 1, tzinfo=UTC)
    assert next_period_end(start, Interval.YEAR) == datetime(2027, 1, 1, tzinfo=UTC)
    assert next_period_end(start, Interval.MONTH, 3) == datetime(2026, 4, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        next_period_end(start, Interval.MONTH, 0)


def test_remaining_fraction_and_clamping():
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC)
    assert remaining_fraction(start, end, datetime(2026, 1, 16, tzinfo=UTC)) == Fraction(1, 2)
    assert remaining_fraction(start, end, datetime(2025, 12, 1, tzinfo=UTC)) == 1
    assert remaining_fraction(start, end, datetime(2026, 2, 9, tzinfo=UTC)) == 0
    with pytest.raises(ValueError):
        remaining_fraction(end, start, start)


def test_prorate_exact_midpoint():
    start, end = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    mid = datetime(2026, 1, 16, 12, tzinfo=UTC)  # 15.5 of 31 days
    assert prorate(310_000, start, end, mid) == 155_000


def test_prorate_change_credit_and_charge_use_same_fraction():
    start, end = datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 2, 15, tzinfo=UTC)
    at = datetime(2026, 2, 10, tzinfo=UTC)  # 5 of 31 days left
    pr = prorate_change(150_000, 400_000, start, end, at)
    assert pr.fraction == Fraction(5, 31)
    assert pr.credit_minor == 24_194
    assert pr.charge_minor == 64_516
    assert pr.net_minor == 40_322
