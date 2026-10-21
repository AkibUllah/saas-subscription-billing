from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from saas_billing.api.deps import get_ctx, get_db, now
from saas_billing.core.states import SubscriptionStatus
from saas_billing.schemas import (
    CancelRequest,
    ChangePlanRequest,
    ChangeResult,
    ChangeSeatsRequest,
    SubscriptionCreate,
    SubscriptionDetail,
    SubscriptionOut,
    UsageCreate,
    UsageOut,
)
from saas_billing.services import customers, plans, subscriptions, webhooks
from saas_billing.services.context import BillingContext

router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])


@router.post("", response_model=SubscriptionDetail, status_code=201)
def create(
    body: SubscriptionCreate,
    db: Session = Depends(get_db),
    ctx: BillingContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    customer = customers.get_customer(db, body.customer_id)
    plan = plans.get_plan(db, body.plan_id)
    coupon = plans.get_coupon_by_code(db, body.coupon_code) if body.coupon_code else None
    sub = subscriptions.create_subscription(
        db, ctx, customer, plan, at, seats=body.seats, coupon=coupon, trial_days=body.trial_days
    )
    webhooks.dispatch(db, ctx.http, at)
    return sub


@router.get("", response_model=list[SubscriptionOut])
def list_(
    response: Response,
    db: Session = Depends(get_db),
    customer_id: str | None = None,
    status: SubscriptionStatus | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
):
    rows, total = subscriptions.list_subscriptions(
        db, customer_id=customer_id, status=status, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/{subscription_id}", response_model=SubscriptionDetail)
def get(subscription_id: str, db: Session = Depends(get_db)):
    return subscriptions.get_subscription(db, subscription_id)


@router.post("/{subscription_id}/change-plan", response_model=ChangeResult)
def change_plan(
    subscription_id: str,
    body: ChangePlanRequest,
    db: Session = Depends(get_db),
    ctx: BillingContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    sub = subscriptions.get_subscription(db, subscription_id)
    plan = plans.get_plan(db, body.plan_id)
    invoice = subscriptions.change_plan(
        db, ctx, sub, plan, at, seats=body.seats, invoice_now=body.invoice_now
    )
    webhooks.dispatch(db, ctx.http, at)
    return ChangeResult(subscription=sub, invoice=invoice)


@router.post("/{subscription_id}/seats", response_model=ChangeResult)
def change_seats(
    subscription_id: str,
    body: ChangeSeatsRequest,
    db: Session = Depends(get_db),
    ctx: BillingContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    sub = subscriptions.get_subscription(db, subscription_id)
    invoice = subscriptions.change_seats(db, ctx, sub, body.seats, at, invoice_now=body.invoice_now)
    webhooks.dispatch(db, ctx.http, at)
    return ChangeResult(subscription=sub, invoice=invoice)


@router.post("/{subscription_id}/pause", response_model=SubscriptionOut)
def pause(subscription_id: str, db: Session = Depends(get_db), at: datetime = Depends(now)):
    return subscriptions.pause(db, subscriptions.get_subscription(db, subscription_id), at)


@router.post("/{subscription_id}/resume", response_model=SubscriptionOut)
def resume(subscription_id: str, db: Session = Depends(get_db), at: datetime = Depends(now)):
    return subscriptions.resume(db, subscriptions.get_subscription(db, subscription_id), at)


@router.post("/{subscription_id}/cancel", response_model=SubscriptionOut)
def cancel(
    subscription_id: str,
    body: CancelRequest | None = None,
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    body = body or CancelRequest()
    sub = subscriptions.get_subscription(db, subscription_id)
    return subscriptions.cancel(
        db, sub, at, at_period_end=body.at_period_end, credit_unused=body.credit_unused
    )


@router.post("/{subscription_id}/reactivate", response_model=SubscriptionOut)
def reactivate(subscription_id: str, db: Session = Depends(get_db), at: datetime = Depends(now)):
    return subscriptions.reactivate(db, subscriptions.get_subscription(db, subscription_id), at)


@router.post("/{subscription_id}/usage", response_model=UsageOut, status_code=201)
def record_usage(
    subscription_id: str,
    body: UsageCreate,
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    sub = subscriptions.get_subscription(db, subscription_id)
    return subscriptions.record_usage(
        db,
        sub,
        body.metric,
        body.quantity,
        at,
        recorded_at=body.recorded_at,
        idempotency_key=body.idempotency_key,
    )
