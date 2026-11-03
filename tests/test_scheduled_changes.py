from datetime import timedelta

import pytest

from saas_billing.core.states import SubscriptionStatus as S
from saas_billing.models import ScheduledAction as A
from saas_billing.models import ScheduledChangeStatus as CS
from saas_billing.models import WebhookEvent
from saas_billing.services import scheduled_changes as sched
from saas_billing.services import subscriptions
from saas_billing.services.errors import Conflict, DomainError
from tests.conftest import T0, make_customer, make_plan, subscribe, team_plan

PERIOD_END = T0 + timedelta(days=31)


@pytest.fixture
def starter_sub(session, ctx):
    return subscribe(session, ctx, make_customer(session, T0), make_plan(session, T0), T0)


def event_types(session):
    return [e.type for e in session.query(WebhookEvent).order_by(WebhookEvent.created_at)]


def test_plan_change_defaults_to_period_end_and_follows_it(session, starter_sub):
    team = team_plan(session, T0)
    change = sched.schedule_change(session, starter_sub, A.CHANGE_PLAN, T0, plan=team, seats=4)
    assert change.status is CS.PENDING
    assert change.at_period_end and change.scheduled_for is None
    assert change.effective_at == PERIOD_END
    starter_sub.current_period_end += timedelta(days=3)  # e.g. a pause/resume shifted it
    assert change.effective_at == PERIOD_END + timedelta(days=3)
    assert "subscription.change_scheduled" in event_types(session)


def test_explicit_date_must_be_in_the_future(session, starter_sub):
    with pytest.raises(DomainError) as exc:
        sched.schedule_change(session, starter_sub, A.PAUSE, T0, effective_at=T0)
    assert exc.value.code == "invalid_effective_at"
    later = T0 + timedelta(days=5)
    change = sched.schedule_change(session, starter_sub, A.PAUSE, T0, effective_at=later)
    assert not change.at_period_end and change.effective_at == later


def test_payload_validation_per_action(session, starter_sub):
    team = team_plan(session, T0)
    cases = [
        (A.CHANGE_PLAN, {}, "plan_required"),
        (A.CHANGE_PLAN, {"plan": team, "seats": 0}, "invalid_request"),
        (A.CHANGE_SEATS, {}, "seats_required"),
        (A.CHANGE_SEATS, {"seats": 2, "plan": team}, "unexpected_plan"),
        (A.PAUSE, {"seats": 2}, "unexpected_seats"),
        (A.RESUME, {}, "effective_at_required"),
    ]
    for action, kwargs, code in cases:
        with pytest.raises(DomainError) as exc:
            sched.schedule_change(session, starter_sub, action, T0, **kwargs)
        assert exc.value.code == code, action


def test_plan_change_rejects_inactive_and_other_currency(session, starter_sub):
    retired = make_plan(session, T0, code="old")
    retired.active = False
    usd = make_plan(session, T0, code="usd", currency="USD")
    with pytest.raises(DomainError, match="not active"):
        sched.schedule_change(session, starter_sub, A.CHANGE_PLAN, T0, plan=retired)
    with pytest.raises(DomainError, match="currency"):
        sched.schedule_change(session, starter_sub, A.CHANGE_PLAN, T0, plan=usd)


def test_one_pending_change_per_action(session, starter_sub):
    sched.schedule_change(session, starter_sub, A.CHANGE_SEATS, T0, seats=3)
    with pytest.raises(Conflict) as exc:
        sched.schedule_change(session, starter_sub, A.CHANGE_SEATS, T0, seats=5)
    assert exc.value.code == "change_already_scheduled"


def test_plan_and_seat_changes_are_mutually_exclusive(session, starter_sub):
    team = team_plan(session, T0)
    sched.schedule_change(session, starter_sub, A.CHANGE_PLAN, T0, plan=team)
    with pytest.raises(Conflict) as exc:
        sched.schedule_change(session, starter_sub, A.CHANGE_SEATS, T0, seats=5)
    assert exc.value.code == "conflicting_change"


def test_pause_and_plan_change_can_both_be_pending(session, starter_sub):
    team = team_plan(session, T0)
    sched.schedule_change(session, starter_sub, A.CHANGE_PLAN, T0, plan=team)
    sched.schedule_change(session, starter_sub, A.PAUSE, T0)
    assert {c.action for c in sched.pending_changes(session, starter_sub)} == {
        A.CHANGE_PLAN,
        A.PAUSE,
    }


def test_resume_needs_a_paused_subscription_or_an_earlier_pause(session, starter_sub):
    resume_at = PERIOD_END + timedelta(days=10)
    with pytest.raises(Conflict) as exc:
        sched.schedule_change(session, starter_sub, A.RESUME, T0, effective_at=resume_at)
    assert exc.value.code == "not_paused"

    sched.schedule_change(session, starter_sub, A.PAUSE, T0)  # at period end
    with pytest.raises(Conflict) as exc:
        sched.schedule_change(session, starter_sub, A.RESUME, T0, effective_at=PERIOD_END)
    assert exc.value.code == "resume_before_pause"
    resume = sched.schedule_change(session, starter_sub, A.RESUME, T0, effective_at=resume_at)
    assert resume.effective_at == resume_at


def test_resume_can_be_scheduled_for_an_already_paused_subscription(session, starter_sub):
    subscriptions.pause(session, starter_sub, T0 + timedelta(days=2))
    at = T0 + timedelta(days=20)
    change = sched.schedule_change(
        session, starter_sub, A.RESUME, T0 + timedelta(days=2), effective_at=at
    )
    assert change.effective_at == at
    # Pausing again is not possible while paused; a plan change for later still is.
    with pytest.raises(DomainError) as exc:
        sched.schedule_change(session, starter_sub, A.PAUSE, T0 + timedelta(days=2))
    assert exc.value.code == "not_active"
    sched.schedule_change(session, starter_sub, A.CHANGE_SEATS, T0 + timedelta(days=2), seats=2)


def test_cannot_schedule_on_canceled_or_cancel_pending_subscription(session, ctx, starter_sub):
    subscriptions.cancel(session, starter_sub, T0 + timedelta(days=1), at_period_end=True)
    with pytest.raises(Conflict) as exc:
        sched.schedule_change(session, starter_sub, A.PAUSE, T0 + timedelta(days=1))
    assert exc.value.code == "cancel_scheduled"
    subscriptions.cancel(session, starter_sub, T0 + timedelta(days=2), at_period_end=False)
    with pytest.raises(DomainError) as exc:
        sched.schedule_change(session, starter_sub, A.PAUSE, T0 + timedelta(days=2))
    assert exc.value.code == "subscription_not_changeable"


def test_cancel_pending_change_keeps_it_for_audit(session, starter_sub):
    change = sched.schedule_change(session, starter_sub, A.CHANGE_SEATS, T0, seats=3)
    sched.cancel_change(session, change, T0 + timedelta(hours=1))
    assert change.status is CS.CANCELED and change.canceled_at == T0 + timedelta(hours=1)
    assert sched.pending_changes(session, starter_sub) == []
    assert [c.id for c in sched.list_changes(session, starter_sub)] == [change.id]
    assert sched.list_changes(session, starter_sub, CS.PENDING) == []
    assert "subscription.change_canceled" in event_types(session)
    with pytest.raises(Conflict) as exc:
        sched.cancel_change(session, change, T0 + timedelta(hours=2))
    assert exc.value.code == "change_not_pending"
    # The slot is free again.
    sched.schedule_change(session, starter_sub, A.CHANGE_SEATS, T0, seats=4)


def test_cancelling_a_pause_with_a_dependent_resume_is_rejected(session, starter_sub):
    pause = sched.schedule_change(session, starter_sub, A.PAUSE, T0)
    resume = sched.schedule_change(
        session, starter_sub, A.RESUME, T0, effective_at=PERIOD_END + timedelta(days=7)
    )
    with pytest.raises(Conflict) as exc:
        sched.cancel_change(session, pause, T0)
    assert exc.value.code == "resume_depends_on_pause"
    sched.cancel_change(session, resume, T0)
    sched.cancel_change(session, pause, T0)
    assert sched.pending_changes(session, starter_sub) == []


def test_get_change_is_scoped_to_its_subscription(session, ctx, starter_sub):
    other = subscribe(
        session, ctx, make_customer(session, T0, email="b@example.com"), starter_sub.plan, T0
    )
    change = sched.schedule_change(session, starter_sub, A.PAUSE, T0)
    assert sched.get_change(session, starter_sub, change.id) is change
    with pytest.raises(DomainError) as exc:
        sched.get_change(session, other, change.id)
    assert exc.value.code == "not_found"
    assert other.status is S.ACTIVE


def test_partial_unique_index_backs_the_one_pending_rule(session, starter_sub):
    from sqlalchemy.exc import IntegrityError

    from saas_billing.models import ScheduledChange

    first = sched.schedule_change(session, starter_sub, A.PAUSE, T0)
    session.commit()

    def row(status):
        return ScheduledChange(
            subscription_id=starter_sub.id,
            action=A.PAUSE,
            status=status,
            created_at=T0,
            updated_at=T0,
        )

    session.add(row(CS.CANCELED))  # history rows do not count
    session.flush()
    session.add(row(CS.PENDING))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert sched.pending_changes(session, starter_sub) == [first]
