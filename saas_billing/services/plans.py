from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.core.coupons import CouponDuration, CouponKind
from saas_billing.core.periods import Interval
from saas_billing.models import Coupon, Plan, PlanMeter
from saas_billing.services.errors import Conflict, DomainError, NotFound


def create_plan(
    session: Session,
    *,
    code: str,
    name: str,
    interval: Interval | str,
    base_amount_minor: int,
    now: datetime,
    currency: str = "BDT",
    per_seat_amount_minor: int = 0,
    included_seats: int = 0,
    trial_days: int = 0,
    interval_count: int = 1,
    meters: list[dict] | None = None,
) -> Plan:
    if session.scalar(select(Plan).where(Plan.code == code)):
        raise Conflict(f"plan code {code!r} already exists", code="duplicate_code")
    if base_amount_minor < 0 or per_seat_amount_minor < 0:
        raise DomainError("plan amounts must be >= 0")
    plan = Plan(
        code=code,
        name=name,
        interval=Interval(interval),
        interval_count=interval_count,
        currency=currency.upper(),
        base_amount_minor=base_amount_minor,
        per_seat_amount_minor=per_seat_amount_minor,
        included_seats=included_seats,
        trial_days=trial_days,
        created_at=now,
    )
    for meter in meters or []:
        plan.meters.append(
            PlanMeter(
                metric=meter["metric"],
                unit_amount_minor=meter["unit_amount_minor"],
                included_units=meter.get("included_units", 0),
            )
        )
    session.add(plan)
    session.flush()
    return plan


def get_plan(session: Session, plan_id: str) -> Plan:
    plan = session.get(Plan, plan_id)
    if plan is None:
        plan = session.scalar(select(Plan).where(Plan.code == plan_id))
    if plan is None:
        raise NotFound(f"plan {plan_id} not found")
    return plan


def list_plans(session: Session, include_inactive: bool = False) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.created_at, Plan.id)
    if not include_inactive:
        stmt = stmt.where(Plan.active.is_(True))
    return list(session.scalars(stmt))


def create_coupon(
    session: Session,
    *,
    code: str,
    kind: CouponKind | str,
    duration: CouponDuration | str,
    now: datetime,
    percent_bps: int | None = None,
    amount_minor: int | None = None,
    duration_in_months: int | None = None,
    max_redemptions: int | None = None,
) -> Coupon:
    kind = CouponKind(kind)
    duration = CouponDuration(duration)
    if session.scalar(select(Coupon).where(Coupon.code == code)):
        raise Conflict(f"coupon code {code!r} already exists", code="duplicate_code")
    if kind is CouponKind.PERCENT and not (percent_bps and 0 < percent_bps <= 10_000):
        raise DomainError("percent coupons need percent_bps in 1..10000")
    if kind is CouponKind.FIXED and not (amount_minor and amount_minor > 0):
        raise DomainError("fixed coupons need a positive amount_minor")
    if duration is CouponDuration.REPEATING and not (duration_in_months and duration_in_months > 0):
        raise DomainError("repeating coupons need duration_in_months")
    coupon = Coupon(
        code=code,
        kind=kind,
        percent_bps=percent_bps if kind is CouponKind.PERCENT else None,
        amount_minor=amount_minor if kind is CouponKind.FIXED else None,
        duration=duration,
        duration_in_months=duration_in_months if duration is CouponDuration.REPEATING else None,
        max_redemptions=max_redemptions,
        created_at=now,
    )
    session.add(coupon)
    session.flush()
    return coupon


def get_coupon_by_code(session: Session, code: str) -> Coupon:
    coupon = session.scalar(select(Coupon).where(Coupon.code == code))
    if coupon is None:
        raise NotFound(f"coupon {code!r} not found")
    return coupon


def list_coupons(session: Session) -> list[Coupon]:
    return list(session.scalars(select(Coupon).order_by(Coupon.created_at, Coupon.id)))


def redeem(coupon: Coupon) -> None:
    if not coupon.active:
        raise DomainError(f"coupon {coupon.code} is inactive", code="coupon_inactive")
    if coupon.max_redemptions is not None and coupon.times_redeemed >= coupon.max_redemptions:
        raise DomainError(f"coupon {coupon.code} is fully redeemed", code="coupon_exhausted")
    coupon.times_redeemed += 1
