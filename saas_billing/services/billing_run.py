"""The billing run: roll over due subscriptions, invoice them, collect, and retry dunning.

Idempotency comes from state, not from bookkeeping: a subscription is only due while
`current_period_end <= as_of`, and rolling it over advances the period in the same
transaction that creates the invoice. Running twice for the same `as_of` finds nothing to do.
The unique `invoices.period_key` is the backstop if two runs ever race.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.core.periods import next_period_end
from saas_billing.core.states import BILLABLE, SubscriptionStatus
from saas_billing.models import BillingRun, Invoice, InvoiceStatus, Subscription
from saas_billing.services import invoicing, payments, webhooks
from saas_billing.services.context import BillingContext
from saas_billing.services.lifecycle import transition
from saas_billing.services.subscriptions import issue_period_invoice

log = logging.getLogger(__name__)

MAX_ROLLOVERS_PER_SUBSCRIPTION = 60


def due_subscriptions(session: Session, as_of: datetime) -> list[Subscription]:
    return list(
        session.scalars(
            select(Subscription)
            .where(
                Subscription.status.in_(list(BILLABLE)), Subscription.current_period_end <= as_of
            )
            .order_by(Subscription.current_period_end, Subscription.id)
        )
    )


def due_dunning_invoices(session: Session, as_of: datetime) -> list[Invoice]:
    return list(
        session.scalars(
            select(Invoice)
            .where(
                Invoice.status == InvoiceStatus.OPEN,
                Invoice.next_payment_attempt_at.is_not(None),
                Invoice.next_payment_attempt_at <= as_of,
            )
            .order_by(Invoice.next_payment_attempt_at, Invoice.number)
        )
    )


def process_subscription(
    session: Session,
    ctx: BillingContext,
    sub: Subscription,
    as_of: datetime,
    run: BillingRun,
    transitions: Counter,
) -> None:
    rollovers = 0
    while (
        sub.status in BILLABLE
        and sub.current_period_end <= as_of
        and rollovers < MAX_ROLLOVERS_PER_SUBSCRIPTION
    ):
        rollovers += 1
        ended = (sub.current_period_start, sub.current_period_end)
        period_end = sub.current_period_end

        if sub.cancel_at_period_end:
            # Final invoice only if something is owed for the period that ended (usage, prorations).
            usage, records = invoicing.usage_lines(session, sub, ended[0], ended[1], as_of)
            lines = usage + invoicing.pending_lines(session, sub)
            if lines:
                invoice = invoicing.finalize_invoice(
                    session,
                    ctx,
                    sub.customer,
                    sub,
                    lines,
                    period_start=ended[0],
                    period_end=ended[1],
                    issued_at=period_end,
                    period_key=f"{sub.id}:final",
                    usage_records=records,
                )
                run.invoices_created += 1
                _collect(session, ctx, invoice, as_of, run)
            sub.cancel_at_period_end = False
            transition(
                session,
                sub,
                SubscriptionStatus.CANCELED,
                period_end,
                reason="canceled_at_period_end",
            )
            transitions["canceled_at_period_end"] += 1
            break

        if sub.status is SubscriptionStatus.TRIALING:
            transition(
                session, sub, SubscriptionStatus.ACTIVE, period_end, reason="trial_converted"
            )
            transitions["trial_converted"] += 1

        new_start = period_end
        new_end = next_period_end(new_start, sub.plan.interval, sub.plan.interval_count)
        sub.current_period_start = new_start
        sub.current_period_end = new_end
        sub.updated_at = as_of
        invoice = issue_period_invoice(
            session,
            ctx,
            sub,
            new_start,
            new_end,
            issued_at=period_end,
            ended_period=ended,
            now=as_of,
        )
        run.invoices_created += 1
        _collect(session, ctx, invoice, as_of, run, transitions)

    if rollovers >= MAX_ROLLOVERS_PER_SUBSCRIPTION:  # pragma: no cover - safety valve
        log.error("subscription %s hit the rollover limit; is as_of sane?", sub.id)


def _collect(
    session: Session,
    ctx: BillingContext,
    invoice: Invoice,
    as_of: datetime,
    run: BillingRun,
    transitions: Counter | None = None,
) -> None:
    status_before = (
        session.get(Subscription, invoice.subscription_id).status
        if invoice.subscription_id
        else None
    )
    paid = payments.collect(session, ctx, invoice, as_of)
    if invoice.attempt_count:
        if paid:
            run.payments_succeeded += 1
        else:
            run.payments_failed += 1
    if transitions is not None and invoice.subscription_id:
        after = session.get(Subscription, invoice.subscription_id).status
        if after is not status_before:
            transitions[f"{status_before.value}->{after.value}"] += 1


def run_billing(session: Session, ctx: BillingContext, as_of: datetime) -> BillingRun:
    run = BillingRun(as_of=as_of, started_at=ctx.clock.now())
    session.add(run)
    session.flush()
    transitions: Counter = Counter()

    for sub in due_subscriptions(session, as_of):
        process_subscription(session, ctx, sub, as_of, run, transitions)
        run.subscriptions_processed += 1
        session.commit()

    for invoice in due_dunning_invoices(session, as_of):
        before = (
            session.get(Subscription, invoice.subscription_id).status
            if invoice.subscription_id
            else None
        )
        paid = payments.collect(session, ctx, invoice, as_of)
        run.payments_succeeded += int(paid)
        run.payments_failed += int(not paid)
        if invoice.subscription_id:
            after = session.get(Subscription, invoice.subscription_id).status
            if after is not before:
                transitions[f"{before.value}->{after.value}"] += 1
        session.commit()

    delivered = webhooks.dispatch(session, ctx.http, as_of)
    run.transitions = dict(transitions) | ({"webhooks_delivered": delivered} if delivered else {})
    run.finished_at = ctx.clock.now()
    session.commit()
    log.info(
        "billing run %s as_of=%s subs=%d invoices=%d paid=%d failed=%d",
        run.id,
        as_of.isoformat(),
        run.subscriptions_processed,
        run.invoices_created,
        run.payments_succeeded,
        run.payments_failed,
    )
    return run


def list_runs(session: Session, limit: int = 50) -> list[BillingRun]:
    return list(
        session.scalars(
            select(BillingRun).order_by(BillingRun.started_at.desc(), BillingRun.id).limit(limit)
        )
    )
