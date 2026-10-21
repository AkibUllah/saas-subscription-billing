"""Demo catalogue shared by `billing seed` and `scripts/simulate.py`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.models import Coupon, Customer, Plan
from saas_billing.services import customers, plans
from saas_billing.services.context import BillingContext

PLANS = [
    dict(
        code="starter", name="Starter", interval="month", base_amount_minor=150_000, trial_days=14
    ),
    dict(
        code="team",
        name="Team",
        interval="month",
        base_amount_minor=400_000,
        per_seat_amount_minor=80_000,
        included_seats=3,
        meters=[dict(metric="sms", unit_amount_minor=50, included_units=1_000)],
    ),
    dict(
        code="business-yearly",
        name="Business (yearly)",
        interval="year",
        base_amount_minor=4_000_000,
    ),
]

COUPONS = [
    dict(
        code="LAUNCH20",
        kind="percent",
        percent_bps=2_000,
        duration="repeating",
        duration_in_months=3,
    ),
    dict(code="WELCOME500", kind="fixed", amount_minor=50_000, duration="once"),
]

CUSTOMERS = [
    dict(name="Dhaka Grocers Ltd", email="billing@dhakagrocers.example", payment_token="tok_ok"),
    dict(
        name="Chattogram Logistics", email="accounts@ctglogistics.example", payment_token="tok_ok"
    ),
    dict(name="Sylhet Tea Traders", email="finance@sylhettea.example", payment_token="tok_ok"),
    dict(name="Khulna Fish Export", email="ap@khulnafish.example", payment_token="tok_decline"),
    dict(name="Rajshahi AgriTech", email="ops@rajagri.example", payment_token="tok_ok"),
]


def seed_demo(session: Session, ctx: BillingContext, now: datetime) -> dict[str, int]:
    created = {"plans": 0, "coupons": 0, "customers": 0}
    for spec in PLANS:
        if session.scalar(select(Plan).where(Plan.code == spec["code"])) is None:
            plans.create_plan(session, now=now, currency=ctx.settings.default_currency, **spec)
            created["plans"] += 1
    for spec in COUPONS:
        if session.scalar(select(Coupon).where(Coupon.code == spec["code"])) is None:
            plans.create_coupon(session, now=now, **spec)
            created["coupons"] += 1
    for spec in CUSTOMERS:
        if session.scalar(select(Customer).where(Customer.email == spec["email"])) is None:
            customers.create_customer(
                session, now=now, currency=ctx.settings.default_currency, **spec
            )
            created["customers"] += 1
    session.flush()
    return created
