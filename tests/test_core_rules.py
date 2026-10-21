import time
from datetime import datetime, timedelta, timezone

import pytest

from saas_billing.core.coupons import (
    CouponDuration,
    CouponKind,
    discount_amount,
    periods_covered,
    still_applies,
)
from saas_billing.core.dunning import max_attempts, next_retry_at, parse_schedule
from saas_billing.core.signing import SignatureError, sign, verify
from saas_billing.core.states import InvalidTransition, assert_transition
from saas_billing.core.states import SubscriptionStatus as S

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_discount_percent_and_fixed_capped_at_subtotal():
    assert discount_amount(100_000, CouponKind.PERCENT, 2000, None) == 20_000
    assert discount_amount(100_000, CouponKind.FIXED, None, 50_000) == 50_000
    assert discount_amount(30_000, CouponKind.FIXED, None, 50_000) == 30_000
    assert discount_amount(0, CouponKind.PERCENT, 2000, None) == 0
    with pytest.raises(ValueError):
        discount_amount(100, CouponKind.PERCENT, None, None)


def test_coupon_periods_covered():
    assert periods_covered(CouponDuration.ONCE, None, 1) == 1
    assert periods_covered(CouponDuration.FOREVER, None, 1) is None
    assert periods_covered(CouponDuration.REPEATING, 3, 1) == 3
    assert periods_covered(CouponDuration.REPEATING, 3, 12) == 1  # yearly plan, 3-month coupon
    assert periods_covered(CouponDuration.REPEATING, 14, 12) == 2
    with pytest.raises(ValueError):
        periods_covered(CouponDuration.REPEATING, None, 1)


def test_still_applies_counts_applications():
    assert still_applies(CouponDuration.ONCE, None, 1, 0)
    assert not still_applies(CouponDuration.ONCE, None, 1, 1)
    assert still_applies(CouponDuration.REPEATING, 3, 1, 2)
    assert not still_applies(CouponDuration.REPEATING, 3, 1, 3)
    assert still_applies(CouponDuration.FOREVER, None, 1, 999)


def test_dunning_schedule_parsing():
    assert parse_schedule("1,3,5,7") == (1, 3, 5, 7)
    assert max_attempts((1, 3, 5, 7)) == 5
    for bad in ("", "0,1", "3,1", "a"):
        with pytest.raises(ValueError):
            parse_schedule(bad)


def test_next_retry_follows_schedule_then_gives_up():
    schedule = (1, 3, 5, 7)
    assert next_retry_at(T0, 1, schedule) == T0 + timedelta(days=1)
    assert next_retry_at(T0, 2, schedule) == T0 + timedelta(days=3)
    assert next_retry_at(T0, 4, schedule) == T0 + timedelta(days=7)
    assert next_retry_at(T0, 5, schedule) is None
    with pytest.raises(ValueError):
        next_retry_at(T0, 0, schedule)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.TRIALING, S.ACTIVE),
        (S.ACTIVE, S.PAST_DUE),
        (S.PAST_DUE, S.UNPAID),
        (S.ACTIVE, S.PAUSED),
        (S.PAUSED, S.ACTIVE),
        (S.UNPAID, S.ACTIVE),
    ],
)
def test_valid_transitions(current, target):
    assert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.CANCELED, S.ACTIVE),
        (S.ACTIVE, S.TRIALING),
        (S.ACTIVE, S.UNPAID),
        (S.PAUSED, S.PAST_DUE),
        (S.TRIALING, S.PAUSED),
    ],
)
def test_invalid_transitions_raise(current, target):
    with pytest.raises(InvalidTransition):
        assert_transition(current, target)


def test_signature_roundtrip_tamper_and_stale():
    body = b'{"id":"evt_1"}'
    now = int(time.time())
    header = sign("secret", now, body)
    verify("secret", body, header, now)
    with pytest.raises(SignatureError):
        verify("secret", b'{"id":"evt_2"}', header, now)
    with pytest.raises(SignatureError):
        verify("other", body, header, now)
    with pytest.raises(SignatureError):
        verify("secret", body, header, now + 1000)
    with pytest.raises(SignatureError):
        verify("secret", body, "garbage", now)
