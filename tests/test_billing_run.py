from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from saas_billing.core.states import SubscriptionStatus as S
from saas_billing.models import Invoice, InvoiceStatus
from saas_billing.services import plans, subscriptions
from tests.conftest import T0, make_customer, make_plan, run, subscribe, team_plan


def count_invoices(session) -> int:
    return session.scalar(select(func.count()).select_from(Invoice))


def invoices_for(session, sub):
    return session.scalars(
        select(Invoice).where(Invoice.subscription_id == sub.id).order_by(Invoice.number)
    ).all()


def test_run_is_idempotent_for_same_as_of(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    as_of = T0 + timedelta(days=31)
    first = run(session, ctx, as_of)
    assert first.subscriptions_processed == 1 and first.invoices_created == 1
    second = run(session, ctx, as_of)
    assert second.subscriptions_processed == 0 and second.invoices_created == 0
    assert count_invoices(session) == 2
    assert sub.current_period_start == as_of


def test_run_catches_up_multiple_periods_with_logical_issue_dates(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    result = run(session, ctx, T0 + timedelta(days=95))  # ~3 periods late
    assert result.invoices_created == 3
    issued = [inv.issued_at for inv in invoices_for(session, sub)]
    assert issued == [
        T0,
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 1, tzinfo=timezone.utc),
    ]
    assert sub.current_period_start == datetime(2026, 4, 1, tzinfo=timezone.utc)


def test_invoice_numbers_are_sequential_per_year(session, ctx):
    cust = make_customer(session, T0)
    plan = make_plan(session, T0)
    sub = subscribe(session, ctx, cust, plan, T0)
    other = make_customer(session, T0, email="two@example.com")
    subscribe(session, ctx, other, plan, T0)
    numbers = sorted(inv.number for inv in session.scalars(select(Invoice)))
    assert numbers == ["INV-2026-000001", "INV-2026-000002"]
    run(session, ctx, datetime(2027, 1, 1, tzinfo=timezone.utc))
    numbers = sorted(inv.number for inv in session.scalars(select(Invoice)))
    assert numbers[-2:] == ["INV-2027-000001", "INV-2027-000002"]  # sequence restarts per year
    assert numbers[-3] == "INV-2026-000024"
    assert len(invoices_for(session, sub)) == 13


def test_vat_applied_unless_tax_exempt(session, ctx):
    plan = make_plan(session, T0)
    taxed = subscribe(session, ctx, make_customer(session, T0), plan, T0)
    exempt = subscribe(
        session, ctx, make_customer(session, T0, email="ngo@example.org", tax_exempt=True), plan, T0
    )
    assert invoices_for(session, taxed)[0].tax_minor == 22_500
    assert invoices_for(session, exempt)[0].tax_minor == 0
    assert invoices_for(session, exempt)[0].total_minor == 150_000


def test_credit_balance_is_applied_to_next_invoice(session, ctx):
    cust = make_customer(session, T0)
    cust.credit_balance_minor = 100_000
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    (inv,) = invoices_for(session, sub)
    assert inv.credit_applied_minor == 100_000
    assert inv.total_minor == 172_500 - 100_000
    assert cust.credit_balance_minor == 0


def test_coupon_durations_over_successive_invoices(session, ctx):
    plan = make_plan(session, T0)
    once = plans.create_coupon(
        session, code="ONCE", kind="fixed", amount_minor=50_000, duration="once", now=T0
    )
    repeating = plans.create_coupon(
        session,
        code="REP",
        kind="percent",
        percent_bps=2000,
        duration="repeating",
        duration_in_months=2,
        now=T0,
    )
    forever = plans.create_coupon(
        session, code="FOREVER", kind="percent", percent_bps=1000, duration="forever", now=T0
    )
    subs = {
        name: subscribe(
            session,
            ctx,
            make_customer(session, T0, email=f"{name}@example.com"),
            plan,
            T0,
            coupon=c,
        )
        for name, c in (("once", once), ("rep", repeating), ("forever", forever))
    }
    run(session, ctx, T0 + timedelta(days=95))  # 4 invoices each
    discounts = {
        name: [inv.discount_minor for inv in invoices_for(session, sub)]
        for name, sub in subs.items()
    }
    assert discounts["once"] == [50_000, 0, 0, 0]
    assert discounts["rep"] == [30_000, 30_000, 0, 0]
    assert discounts["forever"] == [15_000] * 4
    assert invoices_for(session, subs["once"])[0].coupon_code == "ONCE"
    assert subs["rep"].coupon_applications == 2


def test_metered_usage_billed_for_the_period_that_ended(session, ctx):
    cust = make_customer(session, T0)
    sub = subscribe(session, ctx, cust, team_plan(session, T0), T0, seats=3)
    subscriptions.record_usage(session, sub, "sms", 1_800, T0 + timedelta(days=10))
    late = subscriptions.record_usage(session, sub, "sms", 500, T0 + timedelta(days=31, hours=1))
    run(session, ctx, T0 + timedelta(days=31, hours=2))
    first, second = invoices_for(session, sub)
    usage = [ln for ln in second.lines if ln.type.value == "usage"]
    assert len(usage) == 1 and usage[0].quantity == 800 and usage[0].amount_minor == 40_000
    assert late.invoice_id is None  # belongs to the new period


def test_run_records_counts_and_transitions(session, ctx):
    plan = make_plan(session, T0, trial_days=10)
    subscribe(session, ctx, make_customer(session, T0), plan, T0)
    subscribe(
        session,
        ctx,
        make_customer(session, T0, email="bad@example.com", token="tok_decline"),
        plan,
        T0,
    )
    result = run(session, ctx, T0 + timedelta(days=10))
    assert result.subscriptions_processed == 2
    assert result.invoices_created == 2
    assert result.payments_succeeded == 1 and result.payments_failed == 1
    assert result.transitions["trial_converted"] == 2
    assert result.transitions["active->past_due"] == 1
    assert result.finished_at is not None


def test_past_due_subscription_keeps_rolling_over(session, ctx):
    cust = make_customer(session, T0, token="tok_decline")
    sub = subscribe(session, ctx, cust, make_plan(session, T0), T0)
    assert sub.status is S.PAST_DUE
    # cancel before dunning exhausts so we can see rollover behaviour on a past-due sub
    ctx.settings.dunning_schedule_days = "1,3,5,7,40"
    run(session, ctx, T0 + timedelta(days=31))
    assert sub.status is S.PAST_DUE
    assert len(invoices_for(session, sub)) == 2
    assert all(inv.status is InvoiceStatus.OPEN for inv in invoices_for(session, sub))
