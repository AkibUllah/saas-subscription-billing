"""Shared status-transition helper (kept separate to avoid import cycles)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.core.states import SubscriptionStatus, assert_transition
from saas_billing.models import (
    ScheduledChange,
    ScheduledChangeStatus,
    Subscription,
    SubscriptionEvent,
)
from saas_billing.services import webhooks
from saas_billing.services.webhooks import iso


def subscription_payload(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "customer_id": sub.customer_id,
        "plan_id": sub.plan_id,
        "status": sub.status.value,
        "seats": sub.seats,
        "trial_end": iso(sub.trial_end),
        "current_period_start": iso(sub.current_period_start),
        "current_period_end": iso(sub.current_period_end),
        "cancel_at_period_end": sub.cancel_at_period_end,
    }


def scheduled_change_payload(change: ScheduledChange) -> dict:
    return {
        "id": change.id,
        "subscription_id": change.subscription_id,
        "action": change.action.value,
        "status": change.status.value,
        "plan_id": change.plan_id,
        "seats": change.seats,
        "at_period_end": change.at_period_end,
        "effective_at": iso(change.effective_at),
        "failure_reason": change.failure_reason,
    }


def _cancel_scheduled_changes(session: Session, sub: Subscription, now: datetime) -> None:
    """A canceled subscription has no future; drop whatever was still scheduled for it."""
    pending = session.scalars(
        select(ScheduledChange).where(
            ScheduledChange.subscription_id == sub.id,
            ScheduledChange.status == ScheduledChangeStatus.PENDING,
        )
    )
    for change in pending:
        change.status = ScheduledChangeStatus.CANCELED
        change.canceled_at = now
        change.updated_at = now
        webhooks.emit(
            session,
            "subscription.change_canceled",
            scheduled_change_payload(change) | {"reason": "subscription_canceled"},
            now,
        )


def transition(
    session: Session, sub: Subscription, target: SubscriptionStatus, now: datetime, *, reason: str
) -> None:
    current = SubscriptionStatus(sub.status)
    assert_transition(current, target)
    sub.events.append(
        SubscriptionEvent(
            from_status=current.value,
            to_status=target.value,
            reason=reason,
            occurred_at=now,
        )
    )
    sub.status = target
    sub.updated_at = now
    if target in (SubscriptionStatus.CANCELED, SubscriptionStatus.UNPAID):
        sub.ended_at = now
    session.flush()
    if target is SubscriptionStatus.CANCELED:
        _cancel_scheduled_changes(session, sub, now)
    event_type = {
        SubscriptionStatus.CANCELED: "subscription.canceled",
        SubscriptionStatus.UNPAID: "subscription.unpaid",
        SubscriptionStatus.PAUSED: "subscription.paused",
    }.get(target, "subscription.updated")
    payload = subscription_payload(sub) | {"previous_status": current.value, "reason": reason}
    webhooks.emit(session, event_type, payload, now)
