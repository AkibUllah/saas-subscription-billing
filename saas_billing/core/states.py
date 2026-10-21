"""Subscription lifecycle state machine."""

from __future__ import annotations

from enum import Enum


class SubscriptionStatus(str, Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELED = "canceled"
    UNPAID = "unpaid"


S = SubscriptionStatus

TRANSITIONS: dict[SubscriptionStatus, frozenset[SubscriptionStatus]] = {
    S.TRIALING: frozenset({S.ACTIVE, S.PAST_DUE, S.CANCELED}),
    S.ACTIVE: frozenset({S.PAST_DUE, S.PAUSED, S.CANCELED}),
    S.PAST_DUE: frozenset({S.ACTIVE, S.CANCELED, S.UNPAID}),
    S.PAUSED: frozenset({S.ACTIVE, S.CANCELED}),
    S.UNPAID: frozenset({S.ACTIVE, S.CANCELED}),
    S.CANCELED: frozenset(),
}

BILLABLE = frozenset({S.TRIALING, S.ACTIVE, S.PAST_DUE})
"""Statuses whose subscriptions roll over and get invoiced."""

RECURRING_REVENUE = frozenset({S.ACTIVE, S.PAST_DUE})
"""Statuses that count towards MRR."""

TERMINAL = frozenset({S.CANCELED, S.UNPAID})
"""Statuses that count as churn."""


class InvalidTransition(Exception):
    def __init__(self, current: SubscriptionStatus, target: SubscriptionStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move subscription from {current.value} to {target.value}")


def assert_transition(current: SubscriptionStatus, target: SubscriptionStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(current, target)
