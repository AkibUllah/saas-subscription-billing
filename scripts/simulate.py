#!/usr/bin/env python
"""Three months of billing for five customers, in memory, with a fixed clock.

Exercises trials, seat changes with proration, a plan upgrade, metered usage, coupons,
a declining card going through dunning to UNPAID, and a cancel-at-period-end.
Prints a month-by-month MRR/churn table and the invoices that were produced.
"""

from __future__ import annotations

import calendar
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saas_billing.clock import FixedClock  # noqa: E402
from saas_billing.config import Settings  # noqa: E402
from saas_billing.core.money import format_money  # noqa: E402
from saas_billing.database import init_db, make_engine, make_session_factory  # noqa: E402
from saas_billing.demo import seed_demo  # noqa: E402
from saas_billing.gateways.mock import MockGateway  # noqa: E402
from saas_billing.models import Customer, Invoice, Subscription  # noqa: E402
from saas_billing.services import billing_run, metrics, plans, subscriptions  # noqa: E402
from saas_billing.services.context import BillingContext  # noqa: E402

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
MONTHS = 3


def day(offset_days: int) -> datetime:
    return START + timedelta(days=offset_days)


def main() -> None:
    settings = Settings(
        _env_file=None, app_env="test", database_url="sqlite://", api_key=None, log_level="WARNING"
    )
    engine = make_engine(settings.database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    clock = FixedClock(START)
    ctx = BillingContext.from_settings(settings, clock=clock, gateway=MockGateway())

    seed_demo(session, ctx, clock.now())
    customer = {c.email.split("@")[1].split(".")[0]: c for c in session.query(Customer).all()}
    starter = plans.get_plan(session, "starter")
    team = plans.get_plan(session, "team")
    yearly = plans.get_plan(session, "business-yearly")

    subs: dict[str, Subscription] = {}

    def create(key: str, plan, **kwargs) -> None:
        subs[key] = subscriptions.create_subscription(
            session, ctx, customer[key], plan, clock.now(), **kwargs
        )

    # Scheduled actions keyed by day offset from START.
    actions = {
        0: [
            lambda: create("dhakagrocers", starter),  # 14-day trial
            lambda: create("ctglogistics", team, seats=5),
            lambda: create(
                "sylhettea", yearly, coupon=plans.get_coupon_by_code(session, "LAUNCH20")
            ),
            lambda: create("khulnafish", team, seats=3),  # card declines -> dunning
            lambda: create(
                "rajagri",
                starter,
                trial_days=0,
                coupon=plans.get_coupon_by_code(session, "WELCOME500"),
            ),
        ],
        19: [
            lambda: subscriptions.record_usage(
                session, subs["ctglogistics"], "sms", 1_800, clock.now()
            )
        ],
        40: [
            lambda: subscriptions.change_plan(
                session, ctx, subs["dhakagrocers"], team, clock.now(), invoice_now=True
            )
        ],
        45: [
            lambda: subscriptions.change_seats(session, ctx, subs["ctglogistics"], 8, clock.now())
        ],
        50: [
            lambda: subscriptions.record_usage(
                session, subs["ctglogistics"], "sms", 2_400, clock.now()
            )
        ],
        51: [
            lambda: subscriptions.cancel(session, subs["rajagri"], clock.now(), at_period_end=True)
        ],
        78: [
            lambda: subscriptions.record_usage(
                session, subs["ctglogistics"], "sms", 900, clock.now()
            )
        ],
    }

    rows = []
    total_days = sum(calendar.monthrange(START.year, START.month + i)[1] for i in range(MONTHS))
    for offset in range(total_days):
        clock.set(day(offset))
        for action in actions.get(offset, []):
            action()
            session.commit()
        billing_run.run_billing(session, ctx, clock.now())

        tomorrow = day(offset + 1)
        if tomorrow.day == 1:  # last day of a month: snapshot
            as_of = tomorrow - timedelta(seconds=1)
            m = metrics.compute_metrics(
                session, as_of, window_days=calendar.monthrange(as_of.year, as_of.month)[1]
            )
            rows.append((as_of.strftime("%b %Y"), m))

    print(
        f"Simulation: {len(customer)} customers, {START:%d %b %Y} → "
        f"{day(total_days - 1):%d %b %Y}, daily billing runs\n"
    )
    header = (
        f"{'Month':<9}{'Active':>7}{'Trial':>6}{'New':>5}{'Churn':>6}{'Churn %':>9}"
        f"{'MRR':>14}{'ARR':>15}{'Invoiced':>13}{'Collected':>13}"
    )
    print(header)
    print("-" * len(header))
    for label, m in rows:
        print(
            f"{label:<9}{m.active_subscriptions:>7}{m.trialing_subscriptions:>6}{m.new_paying_in_window:>5}"
            f"{m.churned_in_window:>6}{m.churn_rate:>9.1%}{format_money(m.mrr_minor):>14}"
            f"{format_money(m.arr_minor):>15}{format_money(m.invoiced_in_window_minor):>13}"
            f"{format_money(m.collected_in_window_minor):>13}"
        )
    last = rows[-1][1]
    print(
        f"\nTrial conversion: {last.trials_converted}/{last.trials_ended}"
        f" = {last.trial_conversion_rate:.0%}"
    )

    print("\nSubscriptions")
    all_subs = session.query(Subscription).all()
    for sub in sorted(all_subs, key=lambda s: (s.created_at, s.customer.name)):
        session.refresh(sub)
        print(
            f"  {sub.customer.name:<22} {sub.plan.name:<18} seats={sub.seats:<2} "
            f"{sub.status.value:<9} "
            f"period {sub.current_period_start:%d %b %Y} → {sub.current_period_end:%d %b %Y}"
        )

    print("\nInvoices")
    for inv in session.query(Invoice).order_by(Invoice.issued_at, Invoice.number):
        detail = ""
        if inv.discount_minor:
            detail += f" discount {format_money(inv.discount_minor)} ({inv.coupon_code})"
        if inv.credit_applied_minor:
            detail += f" credit {format_money(inv.credit_applied_minor)}"
        if inv.last_payment_error:
            detail += f" [{inv.last_payment_error} × {inv.attempt_count}]"
        print(
            f"  {inv.number}  {inv.issued_at:%d %b}  {inv.customer.name:<22} {inv.status.value:<13}"
            f"{format_money(inv.total_minor):>14}{detail}"
        )


if __name__ == "__main__":
    main()
