"""Scheduled changes applied by the billing run, around period boundaries."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from saas_billing.core.proration import prorate_change
from saas_billing.core.states import SubscriptionStatus as S
from saas_billing.models import Invoice, LineType, WebhookEvent
from saas_billing.models import ScheduledAction as A
from saas_billing.models import ScheduledChangeStatus as CS
from saas_billing.services import scheduled_changes as sched
from saas_billing.services import subscriptions
from tests.conftest import T0, make_customer, make_plan, run, subscribe, team_plan

FEB1 = datetime(2026, 2, 1, tzinfo=timezone.utc)
MAR1 = datetime(2026, 3, 1, tzinfo=timezone.utc)
PRORATION = {LineType.PRORATION_CHARGE, LineType.PRORATION_CREDIT}


def invoices_for(session, sub):
    return session.scalars(
        select(Invoice).where(Invoice.subscription_id == sub.id).order_by(Invoice.number)
    ).all()


def events(session, event_type):
    return session.scalars(select(WebhookEvent).where(WebhookEvent.type == event_type)).all()


def starter_sub(session, ctx, **kw):
    return subscribe(session, ctx, make_customer(session, T0, **kw), make_plan(session, T0), T0)


def test_plan_change_at_period_end_bills_next_period_on_new_plan_without_proration(session, ctx):
    sub = starter_sub(session, ctx)
    team = team_plan(session, T0)
    change = sched.schedule_change(
        session, sub, A.CHANGE_PLAN, T0 + timedelta(days=10), plan=team, seats=5
    )

    early = run(session, ctx, FEB1 - timedelta(seconds=1))
    assert early.subscriptions_processed == 0
    assert change.status is CS.PENDING and sub.plan_id != team.id

    result = run(session, ctx, FEB1)
    assert change.status is CS.APPLIED and change.applied_at == FEB1
    assert sub.plan_id == team.id and sub.seats == 5
    assert sub.current_period_start == FEB1 and sub.current_period_end == MAR1
    _, renewal = invoices_for(session, sub)
    assert renewal.issued_at == FEB1
    assert {line.type for line in renewal.lines} == {LineType.PLAN, LineType.SEATS}
    assert not PRORATION & {line.type for line in renewal.lines}
    assert renewal.subtotal_minor == 400_000 + 2 * 80_000
    assert result.transitions["scheduled_change_plan"] == 1
    (updated,) = [
        e
        for e in events(session, "subscription.updated")
        if e.payload["reason"] == "scheduled_change_plan"
    ]
    assert updated.payload["previous_plan_id"] != team.id

    again = run(session, ctx, FEB1)
    assert again.subscriptions_processed == 0 and again.invoices_created == 0
    assert len(invoices_for(session, sub)) == 2


def test_downgrade_to_yearly_at_period_end_starts_a_yearly_period(session, ctx):
    sub = starter_sub(session, ctx)
    yearly = make_plan(session, T0, code="yearly", interval="year", base=1_500_000)
    sched.schedule_change(session, sub, A.CHANGE_PLAN, T0, plan=yearly)
    run(session, ctx, FEB1 + timedelta(days=3))
    assert sub.current_period_start == FEB1
    assert sub.current_period_end == datetime(2027, 2, 1, tzinfo=timezone.utc)
    _, renewal = invoices_for(session, sub)
    assert [line.type for line in renewal.lines] == [LineType.PLAN]
    assert renewal.subtotal_minor == 1_500_000


def test_explicit_boundary_date_is_also_proration_free(session, ctx):
    sub = starter_sub(session, ctx)
    sched.schedule_change(session, sub, A.CHANGE_SEATS, T0, effective_at=FEB1, seats=3)
    run(session, ctx, FEB1)
    assert sub.seats == 3
    _, renewal = invoices_for(session, sub)
    assert not PRORATION & {line.type for line in renewal.lines}


def test_mid_period_plan_change_is_prorated_at_its_instant(session, ctx):
    sub = starter_sub(session, ctx)
    team = team_plan(session, T0)
    at = T0 + timedelta(days=21)
    sched.schedule_change(session, sub, A.CHANGE_PLAN, T0, plan=team, effective_at=at)

    run(session, ctx, at)
    assert sub.plan_id == team.id
    assert len(invoices_for(session, sub)) == 1  # proration waits for the renewal

    run(session, ctx, FEB1)
    _, renewal = invoices_for(session, sub)
    expected = prorate_change(150_000, 400_000, T0, FEB1, at)
    by_type = {line.type: line.amount_minor for line in renewal.lines}
    assert by_type[LineType.PRORATION_CREDIT] == -expected.credit_minor
    assert by_type[LineType.PRORATION_CHARGE] == expected.charge_minor
    assert by_type[LineType.PLAN] == 400_000


def test_mid_period_seat_change_in_a_catch_up_run_lands_in_the_right_period(session, ctx):
    team = team_plan(session, T0)
    sub = subscribe(session, ctx, make_customer(session, T0), team, T0, seats=3)
    at = FEB1 + timedelta(days=14)  # half of February
    sched.schedule_change(session, sub, A.CHANGE_SEATS, T0, seats=5, effective_at=at)

    run(session, ctx, MAR1)  # one late run covers both renewals and the change between them
    _, feb, mar = invoices_for(session, sub)
    assert feb.subtotal_minor == 400_000  # 3 included seats, before the change
    charge = [line for line in mar.lines if line.type is LineType.PRORATION_CHARGE]
    assert [line.amount_minor for line in charge] == [80_000]  # 2 seats x 800 x 14/28
    assert mar.subtotal_minor == 400_000 + 2 * 80_000 + 80_000


def test_pause_at_period_end_stops_invoicing_and_resume_restarts_the_period(session, ctx):
    sub = starter_sub(session, ctx)
    resume_at = FEB1 + timedelta(days=10, hours=6)
    sched.schedule_change(session, sub, A.PAUSE, T0)
    sched.schedule_change(session, sub, A.RESUME, T0, effective_at=resume_at)

    result = run(session, ctx, FEB1)
    assert sub.status is S.PAUSED and sub.paused_at == FEB1
    assert result.invoices_created == 0 and result.transitions["scheduled_pause"] == 1
    assert len(invoices_for(session, sub)) == 1

    assert run(session, ctx, resume_at - timedelta(seconds=1)).invoices_created == 0

    result = run(session, ctx, resume_at)
    assert sub.status is S.ACTIVE and sub.paused_at is None
    assert result.transitions["scheduled_resume"] == 1
    # Nothing was left of the period at pause time, so the resume starts a full new period.
    assert sub.current_period_start == resume_at
    assert sub.current_period_end == resume_at.replace(month=3)
    _, restart = invoices_for(session, sub)
    assert restart.issued_at == resume_at and restart.period_start == resume_at
    assert restart.subtotal_minor == 150_000
    assert [e.reason for e in sub.events] == [
        "created",
        "scheduled_pause",
        "scheduled_resume",
    ]
    assert run(session, ctx, resume_at).invoices_created == 0


def test_pause_and_resume_inside_one_catch_up_run(session, ctx):
    sub = starter_sub(session, ctx)
    resume_at = FEB1 + timedelta(days=20)
    sched.schedule_change(session, sub, A.PAUSE, T0)
    sched.schedule_change(session, sub, A.RESUME, T0, effective_at=resume_at)
    run(session, ctx, resume_at + timedelta(days=40))
    issued = [inv.issued_at for inv in invoices_for(session, sub)]
    assert issued == [T0, resume_at, resume_at.replace(month=3)]
    assert sub.status is S.ACTIVE


def test_mid_period_pause_keeps_the_unused_time_for_the_resume(session, ctx):
    sub = starter_sub(session, ctx)
    pause_at = T0 + timedelta(days=10)
    sched.schedule_change(session, sub, A.PAUSE, T0, effective_at=pause_at)
    run(session, ctx, pause_at)
    assert sub.status is S.PAUSED and sub.paused_at == pause_at
    resume_at = T0 + timedelta(days=50)
    sched.schedule_change(session, sub, A.RESUME, pause_at, effective_at=resume_at)
    run(session, ctx, resume_at)
    assert sub.current_period_end == resume_at + timedelta(days=21)
    assert len(invoices_for(session, sub)) == 1


def test_period_end_change_waits_while_paused(session, ctx):
    sub = starter_sub(session, ctx)
    team = team_plan(session, T0)
    subscriptions.pause(session, sub, T0 + timedelta(days=31))  # exactly at period end
    sched.schedule_change(session, sub, A.CHANGE_PLAN, T0 + timedelta(days=31), plan=team)
    run(session, ctx, MAR1)
    assert sub.plan_id != team.id  # no running period, nothing comes due
    subscriptions.resume(session, sub, MAR1)
    run(session, ctx, MAR1)
    assert sub.plan_id == team.id
    _, restart = invoices_for(session, sub)
    assert restart.issued_at == MAR1 and restart.subtotal_minor == 400_000


def test_change_that_became_invalid_fails_without_blocking_billing(session, ctx):
    sub = starter_sub(session, ctx)
    team = team_plan(session, T0)
    change = sched.schedule_change(session, sub, A.CHANGE_PLAN, T0, plan=team)
    team.active = False  # retired before the renewal
    result = run(session, ctx, FEB1)
    assert change.status is CS.FAILED and "not active" in change.failure_reason
    assert result.transitions["scheduled_change_failed"] == 1
    _, renewal = invoices_for(session, sub)
    assert renewal.subtotal_minor == 150_000  # billed on the old plan
    (failed,) = events(session, "subscription.change_failed")
    assert failed.payload["id"] == change.id and failed.payload["status"] == "failed"


def test_immediate_cancel_drops_pending_changes(session, ctx):
    sub = starter_sub(session, ctx, token="tok_decline")
    assert sub.status is S.PAST_DUE
    change = sched.schedule_change(session, sub, A.CHANGE_SEATS, T0, seats=2)
    subscriptions.cancel(session, sub, T0 + timedelta(hours=1), at_period_end=False)
    assert change.status is CS.CANCELED
    (dropped,) = events(session, "subscription.change_canceled")
    assert dropped.payload["reason"] == "subscription_canceled"


def test_scheduled_pause_fails_if_subscription_is_past_due_by_then(session, ctx):
    sub = starter_sub(session, ctx)
    change = sched.schedule_change(session, sub, A.PAUSE, T0, effective_at=FEB1 + timedelta(days=2))
    sub.customer.payment_token = "tok_decline"
    run(session, ctx, FEB1 + timedelta(days=2))
    assert sub.status is S.PAST_DUE
    assert change.status is CS.FAILED and "only active" in change.failure_reason


def test_cancel_at_period_end_wins_over_a_change_on_the_same_boundary(session, ctx):
    sub = starter_sub(session, ctx)
    team = team_plan(session, T0)
    change = sched.schedule_change(session, sub, A.CHANGE_PLAN, T0, plan=team)
    subscriptions.cancel(session, sub, T0 + timedelta(days=3), at_period_end=True)
    run(session, ctx, FEB1)
    assert sub.status is S.CANCELED and sub.plan_id != team.id
    assert change.status is CS.CANCELED and change.applied_at is None
    assert len(invoices_for(session, sub)) == 1


def test_trial_converts_onto_the_scheduled_plan(session, ctx):
    trial_plan = make_plan(session, T0, code="trial", trial_days=14)
    sub = subscribe(session, ctx, make_customer(session, T0), trial_plan, T0)
    team = team_plan(session, T0)
    sched.schedule_change(session, sub, A.CHANGE_PLAN, T0, plan=team, seats=4)
    run(session, ctx, T0 + timedelta(days=14))
    assert sub.status is S.ACTIVE and sub.plan_id == team.id
    (first,) = invoices_for(session, sub)
    assert first.subtotal_minor == 400_000 + 80_000
    assert not PRORATION & {line.type for line in first.lines}
