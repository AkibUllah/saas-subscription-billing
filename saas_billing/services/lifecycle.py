"""Shared status-transition helper (kept separate to avoid import cycles)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from saas_billing.core.states import SubscriptionStatus, assert_transition
from saas_billing.models import Subscription, SubscriptionEvent
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
    event_type = {
        SubscriptionStatus.CANCELED: "subscription.canceled",
        SubscriptionStatus.UNPAID: "subscription.unpaid",
        SubscriptionStatus.PAUSED: "subscription.paused",
    }.get(target, "subscription.updated")
    payload = subscription_payload(sub) | {"previous_status": current.value, "reason": reason}
    webhooks.emit(session, event_type, payload, now)
