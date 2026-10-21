from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from saas_billing.api.deps import get_ctx, get_db, now
from saas_billing.schemas import (
    WebhookEndpointCreate,
    WebhookEndpointCreated,
    WebhookEndpointOut,
    WebhookEventOut,
)
from saas_billing.services import webhooks
from saas_billing.services.context import BillingContext

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("/endpoints", response_model=WebhookEndpointCreated, status_code=201)
def create_endpoint(
    body: WebhookEndpointCreate, db: Session = Depends(get_db), at: datetime = Depends(now)
):
    return webhooks.create_endpoint(
        db, body.url, at, secret=body.secret, description=body.description
    )


@router.get("/endpoints", response_model=list[WebhookEndpointOut])
def list_endpoints(db: Session = Depends(get_db)):
    return webhooks.list_endpoints(db)


@router.delete("/endpoints/{endpoint_id}", response_model=WebhookEndpointOut)
def deactivate(endpoint_id: str, db: Session = Depends(get_db)):
    return webhooks.deactivate_endpoint(db, endpoint_id)


@router.get("/events", response_model=list[WebhookEventOut])
def list_events(
    db: Session = Depends(get_db), type: str | None = None, limit: int = Query(100, le=500)
):
    return webhooks.list_events(db, limit=limit, event_type=type)


@router.post("/dispatch", response_model=dict)
def dispatch(
    db: Session = Depends(get_db),
    ctx: BillingContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    return {"delivered": webhooks.dispatch(db, ctx.http, at)}
