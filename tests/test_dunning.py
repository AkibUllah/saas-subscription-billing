from datetime import timedelta

from sqlalchemy import select

from saas_billing.core.states import SubscriptionStatus as S
from saas_billing.models import Invoice, InvoiceStatus
from saas_billing.services import customers, payments
from tests.conftest import T0, make_customer, make_plan, run, subscribe


def the_invoice(session, sub) -> Invoice:
    return session.scalar(select(Invoice).where(Invoice.subscription_id == sub.id))


def test_declined_card_walks_schedule_then_unpaid(session, ctx, gateway):
    cust = make_customer(session, T0, token="tok_decline")
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    inv = the_invoice(session, sub)
    assert sub.status is S.PAST_DUE
    assert inv.attempt_count == 1 and inv.next_payment_attempt_at == T0 + timedelta(days=1)

    expected = [(1, 2, 3), (3, 3, 5), (5, 4, 7)]  # (run day, attempts after, next retry day)
    for day, attempts, next_day in expected:
        run(session, ctx, T0 + timedelta(days=day))
        assert inv.attempt_count == attempts
        assert inv.next_payment_attempt_at == T0 + timedelta(days=next_day)
        assert sub.status is S.PAST_DUE

    run(session, ctx, T0 + timedelta(days=6))  # nothing due yet
    assert inv.attempt_count == 4
    run(session, ctx, T0 + timedelta(days=7))
    assert inv.attempt_count == 5
    assert inv.status is InvoiceStatus.UNCOLLECTIBLE and inv.next_payment_attempt_at is None
    assert sub.status is S.UNPAID and sub.ended_at == T0 + timedelta(days=7)
    assert [a.failure_code for a in inv.payment_attempts] == ["card_declined"] * 5
    assert len(gateway.charges) == 5


def test_recovery_mid_schedule_reactivates(session, ctx):
    cust = make_customer(session, T0, token="tok_fail_then_ok:2")
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    inv = the_invoice(session, sub)
    assert sub.status is S.PAST_DUE
    run(session, ctx, T0 + timedelta(days=1))
    assert inv.status is InvoiceStatus.OPEN and inv.attempt_count == 2
    run(session, ctx, T0 + timedelta(days=3))
    assert inv.status is InvoiceStatus.PAID and inv.paid_at == T0 + timedelta(days=3)
    assert sub.status is S.ACTIVE
    assert [e.reason for e in sub.events] == ["created", "payment_failed", "payment_recovered"]


def test_manual_pay_after_unpaid_reactivates_subscription(session, ctx):
    cust = make_customer(session, T0, token="tok_decline")
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    inv = the_invoice(session, sub)
    run(session, ctx, T0 + timedelta(days=8))  # only one retry per run; walk the schedule
    for day in (9, 10, 11):
        run(session, ctx, T0 + timedelta(days=day))
    assert inv.status is InvoiceStatus.UNCOLLECTIBLE and sub.status is S.UNPAID
    customers.update_payment_token(session, cust, "tok_ok")
    assert payments.collect(session, ctx, inv, T0 + timedelta(days=12))
    assert inv.status is InvoiceStatus.PAID
    assert sub.status is S.ACTIVE


def test_idempotency_key_per_attempt(session, ctx, gateway):
    cust = make_customer(session, T0, token="tok_decline")
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    inv = the_invoice(session, sub)
    run(session, ctx, T0 + timedelta(days=1))
    keys = [c["idempotency_key"] for c in gateway.charges]
    assert keys == [f"{inv.id}:1", f"{inv.id}:2"]
