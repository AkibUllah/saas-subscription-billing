from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from saas_billing.api.deps import get_db, now
from saas_billing.schemas import CouponCreate, CouponOut, PlanCreate, PlanOut
from saas_billing.services import plans

router = APIRouter(tags=["plans"])


@router.post("/v1/plans", response_model=PlanOut, status_code=201)
def create_plan(body: PlanCreate, db: Session = Depends(get_db), at: datetime = Depends(now)):
    data = body.model_dump()
    return plans.create_plan(db, now=at, **data)


@router.get("/v1/plans", response_model=list[PlanOut])
def list_plans(include_inactive: bool = False, db: Session = Depends(get_db)):
    return plans.list_plans(db, include_inactive=include_inactive)


@router.get("/v1/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, db: Session = Depends(get_db)):
    return plans.get_plan(db, plan_id)


@router.delete("/v1/plans/{plan_id}", response_model=PlanOut)
def retire_plan(plan_id: str, db: Session = Depends(get_db)):
    plan = plans.get_plan(db, plan_id)
    plan.active = False
    return plan


@router.post("/v1/coupons", response_model=CouponOut, status_code=201)
def create_coupon(body: CouponCreate, db: Session = Depends(get_db), at: datetime = Depends(now)):
    return plans.create_coupon(db, now=at, **body.model_dump())


@router.get("/v1/coupons", response_model=list[CouponOut])
def list_coupons(db: Session = Depends(get_db)):
    return plans.list_coupons(db)
