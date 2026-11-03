"""Scheduled subscription changes: pause, resume, plan or seat change at a later instant.

A change is either pinned to an instant (`scheduled_for`) or follows the subscription's period
end. Validation happens twice: when the change is scheduled (so obvious mistakes are a 4xx
right away) and again when it is applied, because the subscription may have moved on since.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.core.states import SubscriptionStatus
from saas_billing.models import (
    Plan,
    ScheduledAction,
    ScheduledChange,
    ScheduledChangeStatus,
    Subscription,
)
from saas_billing.services import webhooks
from saas_billing.services.errors import Conflict, DomainError, NotFound
from saas_billing.services.lifecycle import scheduled_change_payload
from saas_billing.services.subscriptions import CHANGEABLE, validate_plan_change

S = SubscriptionStatus
A = ScheduledAction
PENDING = ScheduledChangeStatus.PENDING

# Statuses a plan or seat change can be scheduled from. A paused subscription may queue one
# for the period end after it resumes.
SCHEDULABLE_CHANGES = CHANGEABLE | {S.PAUSED}

# Pairs of actions that cannot both be pending: a plan change already carries a seat count.
EXCLUSIVE = {A.CHANGE_PLAN: A.CHANGE_SEATS, A.CHANGE_SEATS: A.CHANGE_PLAN}


def pending_changes(session: Session, sub: Subscription) -> list[ScheduledChange]:
    return list(
        session.scalars(
            select(ScheduledChange)
            .where(ScheduledChange.subscription_id == sub.id, ScheduledChange.status == PENDING)
            .order_by(ScheduledChange.created_at, ScheduledChange.id)
        )
    )


def list_changes(
    session: Session, sub: Subscription, status: ScheduledChangeStatus | None = None
) -> list[ScheduledChange]:
    stmt = select(ScheduledChange).where(ScheduledChange.subscription_id == sub.id)
    if status is not None:
        stmt = stmt.where(ScheduledChange.status == status)
    return list(session.scalars(stmt.order_by(ScheduledChange.created_at, ScheduledChange.id)))


def get_change(session: Session, sub: Subscription, change_id: str) -> ScheduledChange:
    change = session.get(ScheduledChange, change_id)
    if change is None or change.subscription_id != sub.id:
        raise NotFound(f"scheduled change {change_id} not found")
    return change


def schedule_change(
    session: Session,
    sub: Subscription,
    action: ScheduledAction,
    now: datetime,
    *,
    effective_at: datetime | None = None,
    plan: Plan | None = None,
    seats: int | None = None,
) -> ScheduledChange:
    """Queue `action` for `effective_at`, or for the end of the current period when omitted."""
    action = ScheduledAction(action)
    if sub.status not in SCHEDULABLE_CHANGES:
        raise DomainError(
            f"subscription is {sub.status.value}; changes cannot be scheduled",
            code="subscription_not_changeable",
        )
    if sub.cancel_at_period_end:
        raise Conflict(
            "subscription is set to cancel at period end; reactivate it first",
            code="cancel_scheduled",
        )
    if effective_at is not None and effective_at <= now:
        raise DomainError("effective_at must be in the future", code="invalid_effective_at")
    _validate_payload(sub, action, effective_at, plan, seats)

    pending = {c.action: c for c in pending_changes(session, sub)}
    if action in pending:
        raise Conflict(
            f"a {action.value} change is already scheduled ({pending[action].id}); cancel it first",
            code="change_already_scheduled",
        )
    other = EXCLUSIVE.get(action)
    if other in pending:
        raise Conflict(
            f"a {other.value} change is already scheduled ({pending[other].id}); "
            "use one plan change with seats instead",
            code="conflicting_change",
        )
    if action is A.RESUME and sub.status is not S.PAUSED:
        pause = pending.get(A.PAUSE)
        if pause is None:
            raise Conflict(
                "subscription is not paused and has no scheduled pause", code="not_paused"
            )
        if effective_at <= pause.effective_at:
            raise Conflict(
                "resume must take effect after the scheduled pause", code="resume_before_pause"
            )

    change = ScheduledChange(
        subscription=sub,
        subscription_id=sub.id,
        action=action,
        status=PENDING,
        plan_id=plan.id if plan is not None else None,
        seats=seats,
        scheduled_for=effective_at,
        created_at=now,
        updated_at=now,
    )
    session.add(change)
    session.flush()
    webhooks.emit(session, "subscription.change_scheduled", scheduled_change_payload(change), now)
    return change


def _validate_payload(
    sub: Subscription,
    action: ScheduledAction,
    effective_at: datetime | None,
    plan: Plan | None,
    seats: int | None,
) -> None:
    if action is A.CHANGE_PLAN:
        if plan is None:
            raise DomainError("plan_id is required for change_plan", code="plan_required")
        validate_plan_change(sub, plan, seats, allowed=SCHEDULABLE_CHANGES)
        return
    if plan is not None:
        raise DomainError(f"plan_id is not accepted for {action.value}", code="unexpected_plan")
    if action is A.CHANGE_SEATS:
        if seats is None or seats < 1:
            raise DomainError("seats must be >= 1 for change_seats", code="seats_required")
        return
    if seats is not None:
        raise DomainError(f"seats is not accepted for {action.value}", code="unexpected_seats")
    if action is A.PAUSE and sub.status is not S.ACTIVE:
        raise DomainError("only active subscriptions can be paused", code="not_active")
    if action is A.RESUME and effective_at is None:
        raise DomainError(
            "resume needs an explicit effective_at; a paused subscription has no period end",
            code="effective_at_required",
        )


def cancel_change(session: Session, change: ScheduledChange, now: datetime) -> ScheduledChange:
    if change.status is not PENDING:
        raise Conflict(
            f"scheduled change is {change.status.value}, not pending", code="change_not_pending"
        )
    sub = change.subscription
    if change.action is A.PAUSE and sub.status is not S.PAUSED:
        if any(c.action is A.RESUME for c in pending_changes(session, sub)):
            raise Conflict(
                "a scheduled resume depends on this pause; cancel the resume first",
                code="resume_depends_on_pause",
            )
    change.status = ScheduledChangeStatus.CANCELED
    change.canceled_at = now
    change.updated_at = now
    session.flush()
    webhooks.emit(
        session,
        "subscription.change_canceled",
        scheduled_change_payload(change) | {"reason": "canceled"},
        now,
    )
    return change
