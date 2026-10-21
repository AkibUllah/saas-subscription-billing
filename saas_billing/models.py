from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from saas_billing.core.coupons import CouponDuration, CouponKind
from saas_billing.core.ids import new_id
from saas_billing.core.periods import Interval
from saas_billing.core.states import SubscriptionStatus


class UTCDateTime(TypeDecorator):
    """Store aware UTC datetimes as naive UTC and hand them back aware.

    SQLite drops tzinfo and PostgreSQL `timestamp` has none either; normalising here means the
    rest of the code base never has to compare naive with aware values.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime; use aware UTC values")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def enum_column(enum_cls: type[Enum], length: int) -> SAEnum:
    """Portable enum column: stores the `.value` string, returns enum members, no native type."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON}


class InvoiceStatus(str, Enum):
    OPEN = "open"
    PAID = "paid"
    UNCOLLECTIBLE = "uncollectible"
    VOID = "void"


class LineType(str, Enum):
    PLAN = "plan"
    SEATS = "seats"
    PRORATION_CREDIT = "proration_credit"
    PRORATION_CHARGE = "proration_charge"
    USAGE = "usage"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cus"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    payment_token: Mapped[str | None] = mapped_column(String(100))
    tax_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    credit_balance_minor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime]

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="customer")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("plan"))
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    interval: Mapped[Interval] = mapped_column(enum_column(Interval, 10))
    interval_count: Mapped[int] = mapped_column(Integer, default=1)
    currency: Mapped[str] = mapped_column(String(3), default="BDT")
    base_amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    per_seat_amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    included_seats: Mapped[int] = mapped_column(Integer, default=0)
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]

    meters: Mapped[list[PlanMeter]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def months_per_period(self) -> int:
        return Interval(self.interval).months * self.interval_count

    def recurring_amount_minor(self, seats: int) -> int:
        billable_seats = max(0, seats - self.included_seats)
        return self.base_amount_minor + billable_seats * self.per_seat_amount_minor


class PlanMeter(Base):
    """A metered add-on: `unit_amount_minor` per unit above `included_units` per period."""

    __tablename__ = "plan_meters"
    __table_args__ = (UniqueConstraint("plan_id", "metric", name="uq_plan_meter_metric"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(60))
    unit_amount_minor: Mapped[int] = mapped_column(Integer)
    included_units: Mapped[int] = mapped_column(Integer, default=0)

    plan: Mapped[Plan] = relationship(back_populates="meters")


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cpn"))
    code: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[CouponKind] = mapped_column(enum_column(CouponKind, 10))
    percent_bps: Mapped[int | None] = mapped_column(Integer)
    amount_minor: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[CouponDuration] = mapped_column(enum_column(CouponDuration, 10))
    duration_in_months: Mapped[int | None] = mapped_column(Integer)
    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    times_redeemed: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_status_period_end", "status", "current_period_end"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("sub"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    coupon_id: Mapped[str | None] = mapped_column(ForeignKey("coupons.id"))
    status: Mapped[SubscriptionStatus] = mapped_column(enum_column(SubscriptionStatus, 12))
    seats: Mapped[int] = mapped_column(Integer, default=1)
    trial_end: Mapped[datetime | None]
    current_period_start: Mapped[datetime]
    current_period_end: Mapped[datetime]
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    canceled_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    paused_at: Mapped[datetime | None]
    coupon_applications: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    customer: Mapped[Customer] = relationship(back_populates="subscriptions", lazy="joined")
    plan: Mapped[Plan] = relationship(lazy="joined")
    coupon: Mapped[Coupon | None] = relationship(lazy="joined")
    events: Mapped[list[SubscriptionEvent]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
        order_by="SubscriptionEvent.id",
    )


class SubscriptionEvent(Base):
    """Status history; the metrics module replays it to know what was active when."""

    __tablename__ = "subscription_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(60))
    occurred_at: Mapped[datetime] = mapped_column(index=True)

    subscription: Mapped[Subscription] = relationship(back_populates="events")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("usg"))
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    metric: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime]
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), index=True)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("inv"))
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    # `<subscription_id>:<period_start>` for period invoices; NULL for ad-hoc (proration) invoices.
    # The unique constraint is what makes a billing run safe to re-run.
    period_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    status: Mapped[InvoiceStatus] = mapped_column(enum_column(InvoiceStatus, 15), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    subtotal_minor: Mapped[int] = mapped_column(Integer, default=0)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)
    credit_applied_minor: Mapped[int] = mapped_column(Integer, default=0)
    tax_minor: Mapped[int] = mapped_column(Integer, default=0)
    total_minor: Mapped[int] = mapped_column(Integer, default=0)
    amount_paid_minor: Mapped[int] = mapped_column(Integer, default=0)
    coupon_code: Mapped[str | None] = mapped_column(String(60))
    issued_at: Mapped[datetime]
    due_at: Mapped[datetime]
    paid_at: Mapped[datetime | None]
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_payment_attempt_at: Mapped[datetime | None] = mapped_column(index=True)
    last_payment_error: Mapped[str | None] = mapped_column(String(200))

    customer: Mapped[Customer] = relationship(lazy="joined")
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.id", lazy="selectin"
    )
    payment_attempts: Mapped[list[PaymentAttempt]] = relationship(
        back_populates="invoice", order_by="PaymentAttempt.id", lazy="selectin"
    )

    @property
    def amount_due_minor(self) -> int:
        return max(0, self.total_minor - self.amount_paid_minor)


class InvoiceLine(Base):
    """An invoice line. `invoice_id` is NULL while the line is pending (e.g. a mid-cycle
    seat change waiting for the next period invoice)."""

    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    type: Mapped[LineType] = mapped_column(enum_column(LineType, 20))
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_amount_minor: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    created_at: Mapped[datetime]

    invoice: Mapped[Invoice | None] = relationship(back_populates="lines")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    gateway: Mapped[str] = mapped_column(String(30))
    reference: Mapped[str | None] = mapped_column(String(100))
    failure_code: Mapped[str | None] = mapped_column(String(60))
    attempted_at: Mapped[datetime]

    invoice: Mapped[Invoice] = relationship(back_populates="payment_attempts")


class InvoiceSequence(Base):
    __tablename__ = "invoice_sequences"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class BillingRun(Base):
    __tablename__ = "billing_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("run"))
    as_of: Mapped[datetime]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    subscriptions_processed: Mapped[int] = mapped_column(Integer, default=0)
    invoices_created: Mapped[int] = mapped_column(Integer, default=0)
    payments_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    payments_failed: Mapped[int] = mapped_column(Integer, default=0)
    transitions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("whe"))
    url: Mapped[str] = mapped_column(String(2000))
    secret: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("evt"))
    type: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(index=True)

    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="event", lazy="selectin"
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("webhook_events.id"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None]

    event: Mapped[WebhookEvent] = relationship(back_populates="deliveries")
    endpoint: Mapped[WebhookEndpoint] = relationship(lazy="joined")
