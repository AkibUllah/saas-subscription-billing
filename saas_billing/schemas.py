from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from saas_billing.core.coupons import CouponDuration, CouponKind
from saas_billing.core.periods import Interval
from saas_billing.core.states import SubscriptionStatus
from saas_billing.models import InvoiceStatus, LineType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- customers -----------------------------------------------------------------------------


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    payment_token: str | None = Field(default=None, max_length=100)
    tax_exempt: bool = False


class CustomerUpdate(BaseModel):
    payment_token: str | None = Field(default=None, max_length=100)


class CustomerOut(ORMModel):
    id: str
    name: str
    email: str
    currency: str
    payment_token: str | None
    tax_exempt: bool
    credit_balance_minor: int
    created_at: datetime


# --- plans & coupons -----------------------------------------------------------------------


class MeterIn(BaseModel):
    metric: str = Field(min_length=1, max_length=60)
    unit_amount_minor: int = Field(ge=0)
    included_units: int = Field(default=0, ge=0)


class MeterOut(ORMModel):
    metric: str
    unit_amount_minor: int
    included_units: int


class PlanCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    interval: Interval
    interval_count: int = Field(default=1, ge=1, le=12)
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    base_amount_minor: int = Field(ge=0)
    per_seat_amount_minor: int = Field(default=0, ge=0)
    included_seats: int = Field(default=0, ge=0)
    trial_days: int = Field(default=0, ge=0, le=365)
    meters: list[MeterIn] = Field(default_factory=list)


class PlanOut(ORMModel):
    id: str
    code: str
    name: str
    interval: Interval
    interval_count: int
    currency: str
    base_amount_minor: int
    per_seat_amount_minor: int
    included_seats: int
    trial_days: int
    active: bool
    meters: list[MeterOut]
    created_at: datetime


class CouponCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    kind: CouponKind
    percent_bps: int | None = Field(default=None, ge=1, le=10_000)
    amount_minor: int | None = Field(default=None, ge=1)
    duration: CouponDuration
    duration_in_months: int | None = Field(default=None, ge=1, le=120)
    max_redemptions: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> CouponCreate:
        if self.kind is CouponKind.PERCENT and self.percent_bps is None:
            raise ValueError("percent coupons need percent_bps")
        if self.kind is CouponKind.FIXED and self.amount_minor is None:
            raise ValueError("fixed coupons need amount_minor")
        if self.duration is CouponDuration.REPEATING and self.duration_in_months is None:
            raise ValueError("repeating coupons need duration_in_months")
        return self


class CouponOut(ORMModel):
    id: str
    code: str
    kind: CouponKind
    percent_bps: int | None
    amount_minor: int | None
    duration: CouponDuration
    duration_in_months: int | None
    max_redemptions: int | None
    times_redeemed: int
    active: bool


# --- subscriptions -------------------------------------------------------------------------


class SubscriptionCreate(BaseModel):
    customer_id: str
    plan_id: str = Field(description="Plan id or plan code")
    seats: int = Field(default=1, ge=1, le=10_000)
    coupon_code: str | None = None
    trial_days: int | None = Field(
        default=None, ge=0, le=365, description="Override the plan's trial"
    )


class SubscriptionOut(ORMModel):
    id: str
    customer_id: str
    plan_id: str
    coupon_id: str | None
    status: SubscriptionStatus
    seats: int
    trial_end: datetime | None
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: datetime | None
    ended_at: datetime | None
    paused_at: datetime | None
    coupon_applications: int
    started_at: datetime
    created_at: datetime
    updated_at: datetime


class SubscriptionEventOut(ORMModel):
    from_status: str | None
    to_status: str
    reason: str
    occurred_at: datetime


class SubscriptionDetail(SubscriptionOut):
    plan: PlanOut
    events: list[SubscriptionEventOut]


class ChangePlanRequest(BaseModel):
    plan_id: str
    seats: int | None = Field(default=None, ge=1, le=10_000)
    invoice_now: bool = False


class ChangeSeatsRequest(BaseModel):
    seats: int = Field(ge=1, le=10_000)
    invoice_now: bool = False


class CancelRequest(BaseModel):
    at_period_end: bool = True
    credit_unused: bool = False


class UsageCreate(BaseModel):
    metric: str = Field(min_length=1, max_length=60)
    quantity: int = Field(ge=1)
    recorded_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)


class UsageOut(ORMModel):
    id: str
    subscription_id: str
    metric: str
    quantity: int
    recorded_at: datetime
    idempotency_key: str | None
    invoice_id: str | None


class ChangeResult(BaseModel):
    subscription: SubscriptionOut
    invoice: InvoiceOut | None = None


# --- invoices ------------------------------------------------------------------------------


class InvoiceLineOut(ORMModel):
    type: LineType
    description: str
    quantity: int
    unit_amount_minor: int
    amount_minor: int
    period_start: datetime
    period_end: datetime


class PaymentAttemptOut(ORMModel):
    amount_minor: int
    succeeded: bool
    gateway: str
    reference: str | None
    failure_code: str | None
    attempted_at: datetime


class InvoiceOut(ORMModel):
    id: str
    number: str
    customer_id: str
    subscription_id: str | None
    status: InvoiceStatus
    currency: str
    period_start: datetime
    period_end: datetime
    subtotal_minor: int
    discount_minor: int
    credit_applied_minor: int
    tax_minor: int
    total_minor: int
    amount_paid_minor: int
    amount_due_minor: int
    coupon_code: str | None
    issued_at: datetime
    due_at: datetime
    paid_at: datetime | None
    attempt_count: int
    next_payment_attempt_at: datetime | None
    last_payment_error: str | None
    lines: list[InvoiceLineOut]
    payment_attempts: list[PaymentAttemptOut]


class Page(BaseModel):
    total: int
    items: list[Any]


# --- billing run / metrics / webhooks ------------------------------------------------------


class BillingRunOut(ORMModel):
    id: str
    as_of: datetime
    started_at: datetime
    finished_at: datetime | None
    subscriptions_processed: int
    invoices_created: int
    payments_succeeded: int
    payments_failed: int
    transitions: dict[str, Any]


class MetricsOut(BaseModel):
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


class WebhookEndpointCreate(BaseModel):
    url: str = Field(pattern=r"^https?://", max_length=2000)
    secret: str | None = Field(default=None, min_length=16, max_length=128)
    description: str | None = Field(default=None, max_length=300)


class WebhookEndpointOut(ORMModel):
    id: str
    url: str
    description: str | None
    active: bool
    created_at: datetime


class WebhookEndpointCreated(WebhookEndpointOut):
    secret: str


class WebhookDeliveryOut(ORMModel):
    endpoint_id: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None


class WebhookEventOut(ORMModel):
    id: str
    type: str
    payload: dict[str, Any]
    created_at: datetime
    deliveries: list[WebhookDeliveryOut]


class HealthOut(BaseModel):
    status: str
    database: str
    gateway: str
    clock: datetime
    version: str
