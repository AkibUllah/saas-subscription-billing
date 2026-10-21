from datetime import timedelta

from saas_billing.core.states import SubscriptionStatus as S
from saas_billing.services import plans, subscriptions
from saas_billing.services.metrics import compute_metrics, monthly_recurring_amount, status_at
from tests.conftest import T0, make_customer, make_plan, run, subscribe, team_plan


def test_mrr_normalises_yearly_seats_and_coupons(session, ctx):
    starter = make_plan(session, T0)
    team = team_plan(session, T0)
    yearly = make_plan(session, T0, code="yearly", base=4_000_000, interval="year")
    forever = plans.create_coupon(
        session, code="F10", kind="percent", percent_bps=1000, duration="forever", now=T0
    )
    once = plans.create_coupon(
        session, code="ONCE", kind="fixed", amount_minor=50_000, duration="once", now=T0
    )
    a = subscribe(
        session, ctx, make_customer(session, T0, email="a@x.com"), starter, T0, coupon=once
    )
    b = subscribe(session, ctx, make_customer(session, T0, email="b@x.com"), team, T0, seats=5)
    c = subscribe(
        session, ctx, make_customer(session, T0, email="c@x.com"), yearly, T0, coupon=forever
    )
    assert monthly_recurring_amount(a) == 150_000  # once-coupon already consumed
    assert monthly_recurring_amount(b) == 560_000
    assert monthly_recurring_amount(c) == 300_000  # (4,000,000 - 10%) / 12
    m = compute_metrics(session, T0 + timedelta(days=1))
    assert m.mrr_minor == 1_010_000 and m.arr_minor == 12_120_000
    assert m.active_subscriptions == 3


def test_churn_counts_unpaid_and_canceled_against_window_base(session, ctx):
    plan = make_plan(session, T0)
    good = subscribe(session, ctx, make_customer(session, T0, email="a@x.com"), plan, T0)
    subscribe(session, ctx, make_customer(session, T0, email="b@x.com"), plan, T0)
    subscribe(
        session, ctx, make_customer(session, T0, email="c@x.com", token="tok_decline"), plan, T0
    )
    subscribe(session, ctx, make_customer(session, T0, email="d@x.com"), plan, T0)
    for day in range(1, 32):
        run(session, ctx, T0 + timedelta(days=day))  # declined card -> UNPAID on day 7
    subscriptions.cancel(session, good, T0 + timedelta(days=20), at_period_end=False)
    m = compute_metrics(session, T0 + timedelta(days=31), window_days=31)
    assert m.active_at_window_start == 4  # window opens at T0 exactly; events at T0 count via <=
    assert m.churned_in_window == 2
    assert m.churn_rate == 0.5
    assert m.active_subscriptions == 2
    later = compute_metrics(session, T0 + timedelta(days=90), window_days=30)
    assert later.churned_in_window == 0 and later.active_at_window_start == 2


def test_trial_conversion_and_status_at(session, ctx):
    plan = make_plan(session, T0, trial_days=14)
    converts = subscribe(session, ctx, make_customer(session, T0, email="a@x.com"), plan, T0)
    bails = subscribe(session, ctx, make_customer(session, T0, email="b@x.com"), plan, T0)
    still = subscribe(
        session, ctx, make_customer(session, T0, email="c@x.com"), plan, T0 + timedelta(days=10)
    )
    subscriptions.cancel(session, bails, T0 + timedelta(days=5), at_period_end=False)
    run(session, ctx, T0 + timedelta(days=14))
    m = compute_metrics(session, T0 + timedelta(days=15))
    assert m.trials_ended == 2 and m.trials_converted == 1 and m.trial_conversion_rate == 0.5
    assert m.trialing_subscriptions == 1
    assert status_at(converts, T0 + timedelta(days=13)) is S.TRIALING
    assert status_at(converts, T0 + timedelta(days=14)) is S.ACTIVE
    assert status_at(still, T0 + timedelta(days=1)) is None


def test_invoiced_and_collected_in_window(session, ctx):
    plan = make_plan(session, T0)
    subscribe(session, ctx, make_customer(session, T0, email="a@x.com"), plan, T0)
    subscribe(
        session, ctx, make_customer(session, T0, email="b@x.com", token="tok_decline"), plan, T0
    )
    m = compute_metrics(session, T0 + timedelta(hours=1), window_days=1)
    assert m.invoiced_in_window_minor == 2 * 172_500
    assert m.collected_in_window_minor == 172_500
    assert m.past_due_subscriptions == 1 and m.new_paying_in_window == 2
