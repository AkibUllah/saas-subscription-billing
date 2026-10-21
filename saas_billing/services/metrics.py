"""SaaS metrics: MRR, ARR, churn and trial conversion, computed as of any point in time."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from saas_billing.core.coupons import discount_amount, still_applies
from saas_billing.core.money import round_half_up
from saas_billing.core.states import RECURRING_REVENUE, TERMINAL, SubscriptionStatus
from saas_billing.models import Invoice, PaymentAttempt, Subscription


@dataclass
class Metrics:
    as_of: datetime
    window_days: int
    mrr_minor: int
    arr_minor: int
    active_subscriptions: int
    trialing_subscriptions: int
    past_due_subscriptions: int
    paused_subscriptions: int
    active_at_window_start: int
    new_paying_in_window: int
    churned_in_window: int
    churn_rate: float
    trials_ended: int
    trials_converted: int
    trial_conversion_rate: float
    invoiced_in_window_minor: int
    collected_in_window_minor: int

    def as_dict(self) -> dict:
        return asdict(self)


def monthly_recurring_amount(sub: Subscription) -> int:
    """What the subscription is worth per month right now, net of a coupon that still applies."""
    plan = sub.plan
    amount = plan.recurring_amount_minor(sub.seats)
    coupon = sub.coupon
    if (
        coupon is not None
        and coupon.active
        and still_applies(
            coupon.duration,
            coupon.duration_in_months,
            plan.months_per_period,
            sub.coupon_applications,
        )
    ):
        amount -= discount_amount(amount, coupon.kind, coupon.percent_bps, coupon.amount_minor)
    return round_half_up(Fraction(amount, plan.months_per_period))


def status_at(sub: Subscription, at: datetime) -> SubscriptionStatus | None:
    """Replay the status history; None if the subscription did not exist yet."""
    current: SubscriptionStatus | None = None
    for event in sub.events:
        if event.occurred_at <= at:
            current = SubscriptionStatus(event.to_status)
        else:
            break
    return current


def compute_metrics(session: Session, as_of: datetime, window_days: int = 30) -> Metrics:
    # populate_existing: refresh event collections that may be stale in a long-lived session
    subs = session.scalars(
        select(Subscription)
        .options(selectinload(Subscription.events))
        .execution_options(populate_existing=True)
    ).all()
    subs = [s for s in subs if s.created_at <= as_of]
    window_start = as_of - timedelta(days=window_days)

    def live_status(sub: Subscription) -> SubscriptionStatus | None:
        return status_at(sub, as_of)

    by_status = {}
    mrr = 0
    for sub in subs:
        status = live_status(sub)
        if status is None:
            continue
        by_status[status] = by_status.get(status, 0) + 1
        if status in RECURRING_REVENUE:
            mrr += monthly_recurring_amount(sub)

    active_at_start = sum(1 for s in subs if status_at(s, window_start) in RECURRING_REVENUE)
    churned = 0
    new_paying = 0
    for sub in subs:
        for event in sub.events:
            if not (window_start < event.occurred_at <= as_of):
                continue
            to_status = SubscriptionStatus(event.to_status)
            from_status = SubscriptionStatus(event.from_status) if event.from_status else None
            if to_status in TERMINAL and from_status in RECURRING_REVENUE:
                churned += 1
            if to_status is SubscriptionStatus.ACTIVE and from_status in (
                None,
                SubscriptionStatus.TRIALING,
            ):
                new_paying += 1

    # Churn is measured against everyone who paid at some point in the window: the subscriptions
    # active when it opened plus the ones that started paying during it.
    churn_base = active_at_start + new_paying

    trials_ended = [s for s in subs if s.trial_end is not None and s.trial_end <= as_of]
    converted = sum(
        1
        for s in trials_ended
        if any(
            e.from_status == SubscriptionStatus.TRIALING.value
            and e.to_status == SubscriptionStatus.ACTIVE.value
            and e.occurred_at <= as_of
            for e in s.events
        )
    )

    invoiced_total = sum(
        session.scalars(
            select(Invoice.total_minor).where(
                Invoice.issued_at > window_start, Invoice.issued_at <= as_of
            )
        )
    )
    collected_total = sum(
        session.scalars(
            select(PaymentAttempt.amount_minor).where(
                PaymentAttempt.succeeded.is_(True),
                PaymentAttempt.attempted_at > window_start,
                PaymentAttempt.attempted_at <= as_of,
            )
        )
    )

    return Metrics(
        as_of=as_of,
        window_days=window_days,
        mrr_minor=mrr,
        arr_minor=mrr * 12,
        active_subscriptions=by_status.get(SubscriptionStatus.ACTIVE, 0)
        + by_status.get(SubscriptionStatus.PAST_DUE, 0),
        trialing_subscriptions=by_status.get(SubscriptionStatus.TRIALING, 0),
        past_due_subscriptions=by_status.get(SubscriptionStatus.PAST_DUE, 0),
        paused_subscriptions=by_status.get(SubscriptionStatus.PAUSED, 0),
        active_at_window_start=active_at_start,
        new_paying_in_window=new_paying,
        churned_in_window=churned,
        churn_rate=round(churned / churn_base, 4) if churn_base else 0.0,
        trials_ended=len(trials_ended),
        trials_converted=converted,
        trial_conversion_rate=round(converted / len(trials_ended), 4) if trials_ended else 0.0,
        invoiced_in_window_minor=invoiced_total,
        collected_in_window_minor=collected_total,
    )
