"""Collecting invoices and dunning."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from saas_billing.core.dunning import next_retry_at
from saas_billing.core.states import BILLABLE, SubscriptionStatus
from saas_billing.models import Invoice, InvoiceStatus, PaymentAttempt, Subscription
from saas_billing.services import webhooks
from saas_billing.services.context import BillingContext
from saas_billing.services.errors import DomainError
from saas_billing.services.invoicing import invoice_payload
from saas_billing.services.lifecycle import transition

log = logging.getLogger(__name__)


def collect(session: Session, ctx: BillingContext, invoice: Invoice, now: datetime) -> bool:
    """Try to collect an invoice once. Returns True when the invoice ends up paid.

    - zero-total invoices are marked paid without touching the gateway
    - a failed charge schedules the next retry from the dunning schedule and moves the
      subscription to PAST_DUE; when the schedule is exhausted the invoice becomes
      UNCOLLECTIBLE and the subscription UNPAID
    - a successful charge marks the invoice paid and reactivates a PAST_DUE/UNPAID subscription
    """
    if invoice.status not in (InvoiceStatus.OPEN, InvoiceStatus.UNCOLLECTIBLE):
        raise DomainError(
            f"invoice {invoice.number} is {invoice.status.value}", code="invoice_closed"
        )

    sub: Subscription | None = (
        session.get(Subscription, invoice.subscription_id) if invoice.subscription_id else None
    )
    amount = invoice.amount_due_minor
    if amount == 0:
        _mark_paid(session, invoice, sub, now)
        return True

    idempotency_key = f"{invoice.id}:{invoice.attempt_count + 1}"
    result = ctx.gateway.charge(
        invoice.customer.payment_token, amount, invoice.currency, idempotency_key
    )
    invoice.attempt_count += 1
    invoice.payment_attempts.append(
        PaymentAttempt(
            amount_minor=amount,
            succeeded=result.succeeded,
            gateway=ctx.gateway.name,
            reference=result.reference,
            failure_code=result.failure_code,
            attempted_at=now,
        )
    )

    if result.succeeded:
        invoice.amount_paid_minor += amount
        invoice.last_payment_error = None
        _mark_paid(session, invoice, sub, now)
        return True

    invoice.last_payment_error = result.failure_code
    if invoice.status is InvoiceStatus.UNCOLLECTIBLE:
        # A manual retry on an already written-off invoice: record the attempt, nothing else.
        session.flush()
        return False

    retry_at = next_retry_at(
        invoice.issued_at, invoice.attempt_count, ctx.settings.dunning_schedule
    )
    if retry_at is None:
        invoice.status = InvoiceStatus.UNCOLLECTIBLE
        invoice.next_payment_attempt_at = None
        if sub is not None and sub.status in BILLABLE:
            transition(session, sub, SubscriptionStatus.UNPAID, now, reason="dunning_exhausted")
        webhooks.emit(session, "invoice.uncollectible", invoice_payload(invoice), now)
        log.info("invoice %s written off after %d attempts", invoice.number, invoice.attempt_count)
    else:
        invoice.next_payment_attempt_at = retry_at
        if sub is not None and sub.status in (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
        ):
            transition(session, sub, SubscriptionStatus.PAST_DUE, now, reason="payment_failed")
        webhooks.emit(session, "invoice.payment_failed", invoice_payload(invoice), now)
    session.flush()
    return False


def _mark_paid(session: Session, invoice: Invoice, sub: Subscription | None, now: datetime) -> None:
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = now
    invoice.next_payment_attempt_at = None
    if sub is not None and sub.status in (SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID):
        transition(session, sub, SubscriptionStatus.ACTIVE, now, reason="payment_recovered")
    session.flush()
    webhooks.emit(session, "invoice.paid", invoice_payload(invoice), now)
