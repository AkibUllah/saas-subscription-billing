from datetime import timedelta

import pytest
from sqlalchemy import select

from saas_billing.core.states import SubscriptionStatus as S
from saas_billing.models import Invoice, InvoiceLine, InvoiceStatus, LineType
from saas_billing.services import plans, subscriptions
from saas_billing.services.errors import DomainError
from tests.conftest import T0, make_customer, make_plan, run, subscribe, team_plan


def pending(session, sub):
    return session.scalars(
        select(InvoiceLine).where(
            InvoiceLine.subscription_id == sub.id, InvoiceLine.invoice_id.is_(None)
        )
    ).all()


def invoices_for(session, sub):
    return session.scalars(
        select(Invoice).where(Invoice.subscription_id == sub.id).order_by(Invoice.number)
    ).all()


def test_create_without_trial_invoices_and_charges_immediately(session, ctx, gateway):
    cust = make_customer(session, T0)
    plan = make_plan(session, T0)
    sub = subscribe(session, ctx, cust, plan, T0)
    assert sub.status is S.ACTIVE
    assert sub.current_period_end == T0 + timedelta(days=31)
    (inv,) = invoices_for(session, sub)
    assert inv.status is InvoiceStatus.PAID
    assert inv.subtotal_minor == 150_000 and inv.tax_minor == 22_500 and inv.total_minor == 172_500
    assert gateway.charges[0]["amount_minor"] == 172_500


def test_trial_creates_no_invoice_until_trial_end(session, ctx, gateway):
    cust = make_customer(session, T0)
    plan = make_plan(session, T0, trial_days=14)
    sub = subscribe(session, ctx, cust, plan, T0)
    assert sub.status is S.TRIALING
    assert sub.trial_end == sub.current_period_end == T0 + timedelta(days=14)
    assert invoices_for(session, sub) == []
    run(session, ctx, T0 + timedelta(days=13))
    assert sub.status is S.TRIALING
    run(session, ctx, T0 + timedelta(days=14))
    session.refresh(sub)
    assert sub.status is S.ACTIVE
    assert sub.current_period_start == T0 + timedelta(days=14)
    (inv,) = invoices_for(session, sub)
    assert inv.issued_at == T0 + timedelta(days=14)
    assert inv.status is InvoiceStatus.PAID
    assert [e.reason for e in sub.events] == ["created", "trial_converted"]


def test_seat_increase_creates_pending_proration_swept_into_next_invoice(session, ctx):
    cust = make_customer(session, T0)
    plan = team_plan(session, T0)
    sub = subscribe(session, ctx, cust, plan, T0, seats=5)  # 4000 + 2*800 = 5600
    mid = T0 + timedelta(days=15, hours=12)  # exactly half of 31 days
    assert subscriptions.change_seats(session, ctx, sub, 8, mid) is None
    (line,) = pending(session, sub)
    assert line.type is LineType.PRORATION_CHARGE
    assert line.amount_minor == 120_000  # 3 seats * 800 * 1/2
    run(session, ctx, T0 + timedelta(days=31))
    first, second = invoices_for(session, sub)
    types = [ln.type for ln in second.lines]
    assert types == [LineType.PLAN, LineType.SEATS, LineType.PRORATION_CHARGE]
    assert second.subtotal_minor == 400_000 + 5 * 80_000 + 120_000
    assert pending(session, sub) == []


def test_seat_decrease_credits_and_invoice_now(session, ctx, gateway):
    cust = make_customer(session, T0)
    plan = team_plan(session, T0)
    sub = subscribe(session, ctx, cust, plan, T0, seats=8)
    mid = T0 + timedelta(days=15, hours=12)
    invoice = subscriptions.change_seats(session, ctx, sub, 5, mid, invoice_now=True)
    assert invoice is not None and invoice.period_key is None
    assert invoice.lines[0].type is LineType.PRORATION_CREDIT
    assert invoice.lines[0].amount_minor == -120_000
    assert invoice.total_minor == 0 and invoice.status is InvoiceStatus.PAID
    assert cust.credit_balance_minor == 120_000  # surplus credit carried to the customer
    assert len(gateway.charges) == 1  # no gateway call for a zero invoice


def test_seat_change_during_trial_has_no_proration(session, ctx):
    cust = make_customer(session, T0)
    plan = team_plan(session, T0, trial_days=7)
    sub = subscribe(session, ctx, cust, plan, T0, seats=3)
    subscriptions.change_seats(session, ctx, sub, 10, T0 + timedelta(days=2))
    assert sub.seats == 10 and pending(session, sub) == []


def test_plan_upgrade_same_interval_prorates_credit_and_charge(session, ctx):
    cust = make_customer(session, T0)
    starter = make_plan(session, T0)
    team = team_plan(session, T0)
    sub = subscribe(session, ctx, cust, starter, T0)
    at = T0 + timedelta(days=15, hours=12)
    invoice = subscriptions.change_plan(session, ctx, sub, team, at, invoice_now=True)
    assert sub.plan_id == team.id
    assert sub.current_period_end == T0 + timedelta(days=31)  # anchor kept
    credit, charge = invoice.lines
    assert (credit.type, credit.amount_minor) == (LineType.PRORATION_CREDIT, -75_000)
    assert (charge.type, charge.amount_minor) == (LineType.PRORATION_CHARGE, 200_000)
    assert invoice.subtotal_minor == 125_000
    assert invoice.total_minor == 143_750  # + 15% VAT


def test_plan_change_to_yearly_resets_anchor_and_invoices_full_period(session, ctx):
    cust = make_customer(session, T0)
    starter = make_plan(session, T0)
    yearly = make_plan(session, T0, code="business-yearly", base=4_000_000, interval="year")
    sub = subscribe(session, ctx, cust, starter, T0)
    at = T0 + timedelta(days=15, hours=12)
    invoice = subscriptions.change_plan(session, ctx, sub, yearly, at)
    assert sub.current_period_start == at
    assert sub.current_period_end == at.replace(year=2027)
    assert invoice is not None and invoice.period_key == f"{sub.id}:{at.isoformat()}"
    assert {ln.type for ln in invoice.lines} == {LineType.PLAN, LineType.PRORATION_CREDIT}
    assert invoice.subtotal_minor == 4_000_000 - 75_000


def test_change_plan_rejects_other_currency_and_inactive(session, ctx):
    cust = make_customer(session, T0)
    starter = make_plan(session, T0)
    usd = make_plan(session, T0, code="usd", currency="USD")
    retired = make_plan(session, T0, code="old")
    retired.active = False
    sub = subscribe(session, ctx, cust, starter, T0)
    with pytest.raises(DomainError, match="currency"):
        subscriptions.change_plan(session, ctx, sub, usd, T0)
    with pytest.raises(DomainError, match="not active"):
        subscriptions.change_plan(session, ctx, sub, retired, T0)


def test_pause_skips_billing_and_resume_extends_period(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    subscriptions.pause(session, sub, T0 + timedelta(days=10))
    assert sub.status is S.PAUSED
    run(session, ctx, T0 + timedelta(days=40))
    assert len(invoices_for(session, sub)) == 1  # nothing billed while paused
    subscriptions.resume(session, sub, T0 + timedelta(days=40))
    assert sub.status is S.ACTIVE
    # 21 days were left at pause time, so the period now ends 21 days after resuming
    assert sub.current_period_end == T0 + timedelta(days=61)
    with pytest.raises(DomainError):
        subscriptions.resume(session, sub, T0 + timedelta(days=41))


def test_cancel_at_period_end_then_run_cancels_without_new_invoice(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    subscriptions.cancel(session, sub, T0 + timedelta(days=5), at_period_end=True)
    assert sub.status is S.ACTIVE and sub.cancel_at_period_end
    subscriptions.reactivate(session, sub, T0 + timedelta(days=6))
    assert not sub.cancel_at_period_end
    subscriptions.cancel(session, sub, T0 + timedelta(days=7), at_period_end=True)
    run(session, ctx, T0 + timedelta(days=31))
    session.refresh(sub)
    assert sub.status is S.CANCELED
    assert sub.ended_at == T0 + timedelta(days=31)
    assert len(invoices_for(session, sub)) == 1


def test_cancel_at_period_end_bills_outstanding_usage_on_final_invoice(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, team_plan(session, T0), T0, seats=3)
    subscriptions.record_usage(session, sub, "sms", 1_500, T0 + timedelta(days=10))
    subscriptions.cancel(session, sub, T0 + timedelta(days=12))
    run(session, ctx, T0 + timedelta(days=31))
    first, final = invoices_for(session, sub)
    assert final.period_key == f"{sub.id}:final"
    assert [ln.type for ln in final.lines] == [LineType.USAGE]
    assert final.subtotal_minor == 500 * 50


def test_cancel_immediately_voids_open_invoice_and_credits_unused(session, ctx):
    cust = make_customer(session, T0, token="tok_decline")
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    (inv,) = invoices_for(session, sub)
    assert inv.status is InvoiceStatus.OPEN and sub.status is S.PAST_DUE
    subscriptions.cancel(session, sub, T0 + timedelta(days=2), at_period_end=False)
    assert sub.status is S.CANCELED and inv.status is InvoiceStatus.VOID
    assert inv.next_payment_attempt_at is None

    paid_cust = make_customer(session, T0, email="x@y.example")
    paid_sub = subscribe(session, ctx, paid_cust, plans.get_plan(session, "starter"), T0)
    subscriptions.cancel(
        session,
        paid_sub,
        T0 + timedelta(days=15, hours=12),
        at_period_end=False,
        credit_unused=True,
    )
    assert paid_cust.credit_balance_minor == 75_000
    with pytest.raises(DomainError, match="already canceled"):
        subscriptions.cancel(session, paid_sub, T0 + timedelta(days=16))


def test_changes_rejected_on_canceled_subscription(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    subscriptions.cancel(session, sub, T0, at_period_end=False)
    with pytest.raises(DomainError, match="not allowed"):
        subscriptions.change_seats(session, ctx, sub, 2, T0)
    with pytest.raises(DomainError, match="only active"):
        subscriptions.pause(session, sub, T0)


def test_usage_validation_and_idempotency(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, team_plan(session, T0), T0)
    first = subscriptions.record_usage(session, sub, "sms", 100, T0, idempotency_key="k1")
    again = subscriptions.record_usage(session, sub, "sms", 100, T0, idempotency_key="k1")
    assert first.id == again.id
    with pytest.raises(DomainError, match="no metered add-on"):
        subscriptions.record_usage(session, sub, "emails", 1, T0)
    with pytest.raises(DomainError, match="positive"):
        subscriptions.record_usage(session, sub, "sms", 0, T0)


def test_create_validations(session, ctx):
    cust = make_customer(session, T0)
    plan = make_plan(session, T0)
    with pytest.raises(DomainError, match="seats"):
        subscribe(session, ctx, cust, plan, T0, seats=0)
    usd = make_plan(session, T0, code="usd", currency="USD")
    with pytest.raises(DomainError, match="currency"):
        subscribe(session, ctx, cust, usd, T0)
    coupon = plans.create_coupon(
        session,
        code="ONE",
        kind="fixed",
        duration="once",
        amount_minor=100,
        now=T0,
        max_redemptions=1,
    )
    subscribe(session, ctx, cust, plan, T0, coupon=coupon)
    with pytest.raises(DomainError, match="fully redeemed"):
        subscribe(session, ctx, cust, plan, T0, coupon=coupon)
