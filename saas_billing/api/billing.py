from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from saas_billing.api.deps import get_ctx, get_db, now
from saas_billing.clock import ensure_utc
from saas_billing.schemas import BillingRunOut, MetricsOut
from saas_billing.services import billing_run, metrics
from saas_billing.services.context import BillingContext

router = APIRouter(tags=["billing"])


@router.post("/v1/billing/run", response_model=BillingRunOut)
def run(
    as_of: datetime | None = Query(
        default=None, description="Process everything due at this instant (default: now)"
    ),
    db: Session = Depends(get_db),
    ctx: BillingContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    return billing_run.run_billing(db, ctx, ensure_utc(as_of) if as_of else at)


@router.get("/v1/billing/runs", response_model=list[BillingRunOut])
def runs(db: Session = Depends(get_db), limit: int = Query(50, le=500)):
    return billing_run.list_runs(db, limit=limit)


@router.get("/v1/metrics", response_model=MetricsOut)
def get_metrics(
    as_of: datetime | None = None,
    window_days: int = Query(30, ge=1, le=366),
    db: Session = Depends(get_db),
    at: datetime = Depends(now),
):
    return metrics.compute_metrics(db, ensure_utc(as_of) if as_of else at, window_days).as_dict()
