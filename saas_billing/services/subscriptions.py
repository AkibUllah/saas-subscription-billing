"""Subscription lifecycle: create, change plan/seats with proration, pause, cancel, usage."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saas_billing.core.periods import next_period_end
from saas_billing.core.proration import prorate, prorate_change
from saas_billing.core.states import BILLABLE, SubscriptionStatus
from saas_billing.models import (
    Coupon,
    Customer,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    LineType,
    Plan,
    Subscription,
    SubscriptionEvent,
    UsageRecord,
)
from saas_billing.services import invoicing, payments, plans, webhooks
from saas_billing.services.context import BillingContext
from saas_billing.services.errors import DomainError, NotFound
from saas_billing.services.lifecycle import subscription_payload, transition

S = SubscriptionStatus
CHANGEABLE = frozenset({S.TRIALING, S.ACTIVE, S.PAST_DUE})


def period_key(sub: Subscription, period_start: datetime) -> str:
    return f"{sub.id}:{period_start.isoformat()}"


def get_subscription(session: Session, subscription_id: str) -> Subscription:
    sub = session.get(Subscription, subscription_id)
    if sub is None:
        raise NotFound(f"subscription {subscription_id} not found")
    return sub


def list_subscriptions(
    session: Session,
    *,
    customer_id: str | None = None,
    status: SubscriptionStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Subscription], int]:
    stmt = select(Subscription)
    if customer_id:
        stmt = stmt.where(Subscription.customer_id == customer_id)
    if status:
        stmt = stmt.where(Subscription.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Subscription.created_at, Subscription.id).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def create_subscription(
    session: Session,
    ctx: BillingContext,
    customer: Customer,
    plan: Plan,
    now: datetime,
    *,
    seats: int = 1,
    coupon: Coupon | None = None,
    trial_days: int | None = None,
) -> Subscription:
    if not plan.active:
        raise DomainError(f"plan {plan.code} is not active", code="plan_inactive")
    if plan.currency != customer.currency:
        raise DomainError(
            "plan currency does not match customer currency", code="currency_mismatch"
        )
    if seats < 1:
        raise DomainError("seats must be >= 1")
    trial = plan.trial_days if trial_days is None else trial_days
    if trial < 0:
        raise DomainError("trial_days must be >= 0")
    if coupon is not None:
        plans.redeem(coupon)

    sub = Subscription(
        customer_id=customer.id,
        plan_id=plan.id,
        coupon_id=coupon.id if coupon else None,
        seats=seats,
        started_at=now,
        created_at=now,
        updated_at=now,
        current_period_start=now,
    )
    if trial > 0:
        sub.status = S.TRIALING
        sub.trial_end = now + timedelta(days=trial)
        sub.current_period_end = sub.trial_end
    else:
        sub.status = S.ACTIVE
        sub.current_period_end = next_period_end(now, plan.interval, plan.interval_count)
    session.add(sub)
    session.flush()
    session.add(
        SubscriptionEvent(
            subscription_id=sub.id,
            from_status=None,
            to_status=sub.status.value,
            reason="created",
            occurred_at=now,
        )
    )
    session.refresh(sub)
    webhooks.emit(session, "subscription.created", subscription_payload(sub), now)

    if sub.status is S.ACTIVE:
        invoice = issue_period_invoice(
            session,
            ctx,
            sub,
            sub.current_period_start,
            sub.current_period_end,
            issued_at=now,
            ended_period=None,
            now=now,
        )
        payments.collect(session, ctx, invoice, now)
    return sub


def issue_period_invoice(
    session: Session,
    ctx: BillingContext,
    sub: Subscription,
    period_start: datetime,
    period_end: datetime,
    *,
    issued_at: datetime,
    ended_period: tuple[datetime, datetime] | None,
    now: datetime,
) -> Invoice:
    """Invoice for the period that is starting: recurring charges in advance, plus metered
    usage from the period that just ended and any pending proration items."""
    lines = invoicing.recurring_lines(sub, period_start, period_end, now)
    usage_records: list[UsageRecord] = []
    if ended_period is not None:
        usage, usage_records = invoicing.usage_lines(
            session, sub, ended_period[0], ended_period[1], now
        )
        lines.extend(usage)
    lines.extend(invoicing.pending_lines(session, sub))
    return invoicing.finalize_invoice(
        session,
        ctx,
        sub.customer,
        sub,
        lines,
        period_start=period_start,
        period_end=period_end,
        issued_at=issued_at,
        period_key=period_key(sub, period_start),
        usage_records=usage_records,
    )


def _adhoc_invoice(
    session: Session, ctx: BillingContext, sub: Subscription, now: datetime
) -> Invoice:
    """Invoice every pending (proration) line right now instead of waiting for the rollover."""
    lines = invoicing.pending_lines(session, sub)
    invoice = invoicing.finalize_invoice(
        session,
        ctx,
        sub.customer,
        sub,
        lines,
        period_start=now,
        period_end=sub.current_period_end,
        issued_at=now,
        period_key=None,
    )
    payments.collect(session, ctx, invoice, now)
    return invoice


def _line(
    sub: Subscription,
    line_type: LineType,
    description: str,
    amount: int,
    start: datetime,
    end: datetime,
    now: datetime,
) -> InvoiceLine:
    return InvoiceLine(
        subscription_id=sub.id,
        type=line_type,
        description=description,
        quantity=1,
        unit_amount_minor=amount,
        amount_minor=amount,
        period_start=start,
        period_end=end,
        created_at=now,
    )


def _require_changeable(sub: Subscription) -> None:
    if sub.status not in CHANGEABLE:
        raise DomainError(
            f"subscription is {sub.status.value}; changes are not allowed",
            code="subscription_not_changeable",
        )


def change_seats(
    session: Session,
    ctx: BillingContext,
    sub: Subscription,
    seats: int,
    now: datetime,
    *,
    invoice_now: bool = False,
) -> Invoice | None:
    _require_changeable(sub)
    if seats < 1:
        raise DomainError("seats must be >= 1")
    if seats == sub.seats:
        return None
    plan = sub.plan
    lines: list[InvoiceLine] = []
    if sub.status is not S.TRIALING:
        delta = plan.recurring_amount_minor(seats) - plan.recurring_amount_minor(sub.seats)
        prorated = prorate(abs(delta), sub.current_period_start, sub.current_period_end, now)
        if prorated:
            label = invoicing.period_label(now, sub.current_period_end)
            if delta > 0:
                lines.append(
                    _line(
                        sub,
                        LineType.PRORATION_CHARGE,
                        f"Seats {sub.seats} → {seats}: remaining time ({label})",
                        prorated,
                        now,
                        sub.current_period_end,
                        now,
                    )
                )
            else:
                lines.append(
                    _line(
                        sub,
                        LineType.PRORATION_CREDIT,
                        f"Seats {sub.seats} → {seats}: unused time ({label})",
                        -prorated,
                        now,
                        sub.current_period_end,
                        now,
                    )
                )
    previous = sub.seats
    sub.seats = seats
    sub.updated_at = now
    session.add_all(lines)
    session.flush()
    webhooks.emit(
        session,
        "subscription.updated",
        subscription_payload(sub) | {"previous_seats": previous, "reason": "seats_changed"},
        now,
    )
    if invoice_now and invoicing.pending_lines(session, sub):
        return _adhoc_invoice(session, ctx, sub, now)
    return None


def change_plan(
    session: Session,
    ctx: BillingContext,
    sub: Subscription,
    new_plan: Plan,
    now: datetime,
    *,
    seats: int | None = None,
    invoice_now: bool = False,
) -> Invoice | None:
    _require_changeable(sub)
    if not new_plan.active:
        raise DomainError(f"plan {new_plan.code} is not active", code="plan_inactive")
    if new_plan.currency != sub.plan.currency:
        raise DomainError("cannot change to a plan in another currency", code="currency_mismatch")
    new_seats = sub.seats if seats is None else seats
    if new_seats < 1:
        raise DomainError("seats must be >= 1")
    old_plan = sub.plan
    if new_plan.id == old_plan.id and new_seats == sub.seats:
        return None

    invoice: Invoice | None = None
    if sub.status is S.TRIALING:
        # Nothing has been paid yet; the trial simply continues on the new plan.
        sub.plan = new_plan
        sub.plan_id = new_plan.id
        sub.seats = new_seats
    elif new_plan.months_per_period == old_plan.months_per_period:
        old_amount = old_plan.recurring_amount_minor(sub.seats)
        new_amount = new_plan.recurring_amount_minor(new_seats)
        pr = prorate_change(
            old_amount, new_amount, sub.current_period_start, sub.current_period_end, now
        )
        label = invoicing.period_label(now, sub.current_period_end)
        lines: list[InvoiceLine] = []
        if pr.credit_minor:
            lines.append(
                _line(
                    sub,
                    LineType.PRORATION_CREDIT,
                    f"Unused time on {old_plan.name} ({label})",
                    -pr.credit_minor,
                    now,
                    sub.current_period_end,
                    now,
                )
            )
        if pr.charge_minor:
            lines.append(
                _line(
                    sub,
                    LineType.PRORATION_CHARGE,
                    f"Remaining time on {new_plan.name} ({label})",
                    pr.charge_minor,
                    now,
                    sub.current_period_end,
                    now,
                )
            )
        sub.plan = new_plan
        sub.plan_id = new_plan.id
        sub.seats = new_seats
        session.add_all(lines)
        session.flush()
        if invoice_now and invoicing.pending_lines(session, sub):
            invoice = _adhoc_invoice(session, ctx, sub, now)
    else:
        # Interval changes (monthly <-> yearly) reset the billing anchor: credit what is unused,
        # start a fresh period today and invoice it immediately.
        old_amount = old_plan.recurring_amount_minor(sub.seats)
        credit = prorate(old_amount, sub.current_period_start, sub.current_period_end, now)
        label = invoicing.period_label(now, sub.current_period_end)
        if credit:
            session.add(
                _line(
                    sub,
                    LineType.PRORATION_CREDIT,
                    f"Unused time on {old_plan.name} ({label})",
                    -credit,
                    now,
                    sub.current_period_end,
                    now,
                )
            )
        ended = (sub.current_period_start, now)
        sub.plan = new_plan
        sub.plan_id = new_plan.id
        sub.seats = new_seats
        sub.current_period_start = now
        sub.current_period_end = next_period_end(now, new_plan.interval, new_plan.interval_count)
        session.flush()
        invoice = issue_period_invoice(
            session,
            ctx,
            sub,
            now,
            sub.current_period_end,
            issued_at=now,
            ended_period=ended,
            now=now,
        )
        payments.collect(session, ctx, invoice, now)

    sub.updated_at = now
    session.flush()
    webhooks.emit(
        session,
        "subscription.updated",
        subscription_payload(sub) | {"previous_plan_id": old_plan.id, "reason": "plan_changed"},
        now,
    )
    return invoice


def pause(session: Session, sub: Subscription, now: datetime) -> Subscription:
    if sub.status is not S.ACTIVE:
        raise DomainError("only active subscriptions can be paused", code="not_active")
    sub.paused_at = now
    transition(session, sub, S.PAUSED, now, reason="paused")
    return sub


def resume(session: Session, sub: Subscription, now: datetime) -> Subscription:
    if sub.status is not S.PAUSED or sub.paused_at is None:
        raise DomainError("subscription is not paused", code="not_paused")
    remaining = sub.current_period_end - sub.paused_at
    sub.current_period_end = now + max(remaining, timedelta(0))
    sub.paused_at = None
    transition(session, sub, S.ACTIVE, now, reason="resumed")
    return sub


def cancel(
    session: Session,
    sub: Subscription,
    now: datetime,
    *,
    at_period_end: bool = True,
    credit_unused: bool = False,
) -> Subscription:
    if sub.status is S.CANCELED:
        raise DomainError("subscription is already canceled", code="already_canceled")
    if at_period_end and sub.status in BILLABLE | {S.PAUSED}:
        sub.cancel_at_period_end = True
        sub.canceled_at = now
        sub.updated_at = now
        session.flush()
        webhooks.emit(
            session,
            "subscription.updated",
            subscription_payload(sub) | {"reason": "cancel_scheduled"},
            now,
        )
        return sub

    if credit_unused and sub.status is S.ACTIVE:
        credit = prorate(
            sub.plan.recurring_amount_minor(sub.seats),
            sub.current_period_start,
            sub.current_period_end,
            now,
        )
        sub.customer.credit_balance_minor += credit
    for invoice in session.scalars(
        select(Invoice).where(
            Invoice.subscription_id == sub.id, Invoice.status == InvoiceStatus.OPEN
        )
    ):
        invoice.status = InvoiceStatus.VOID
        invoice.next_payment_attempt_at = None
    sub.canceled_at = now
    sub.cancel_at_period_end = False
    transition(session, sub, S.CANCELED, now, reason="canceled_immediately")
    return sub


def reactivate(session: Session, sub: Subscription, now: datetime) -> Subscription:
    """Undo a scheduled cancellation."""
    if not sub.cancel_at_period_end or sub.status is S.CANCELED:
        raise DomainError("subscription has no pending cancellation", code="not_scheduled")
    sub.cancel_at_period_end = False
    sub.canceled_at = None
    sub.updated_at = now
    session.flush()
    webhooks.emit(
        session,
        "subscription.updated",
        subscription_payload(sub) | {"reason": "cancel_revoked"},
        now,
    )
    return sub


def record_usage(
    session: Session,
    sub: Subscription,
    metric: str,
    quantity: int,
    now: datetime,
    *,
    recorded_at: datetime | None = None,
    idempotency_key: str | None = None,
) -> UsageRecord:
    if sub.status not in BILLABLE:
        raise DomainError(
            f"subscription is {sub.status.value}; usage is not accepted",
            code="subscription_not_billable",
        )
    if metric not in {m.metric for m in sub.plan.meters}:
        raise DomainError(
            f"plan {sub.plan.code} has no metered add-on {metric!r}", code="unknown_metric"
        )
    if quantity <= 0:
        raise DomainError("quantity must be positive")
    if idempotency_key:
        existing = session.scalar(
            select(UsageRecord).where(UsageRecord.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing
    record = UsageRecord(
        subscription_id=sub.id,
        metric=metric,
        quantity=quantity,
        recorded_at=recorded_at or now,
        idempotency_key=idempotency_key,
    )
    session.add(record)
    session.flush()
    return record
