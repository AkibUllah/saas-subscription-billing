"""Outgoing webhook events: stored first, delivered with an HMAC signature."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.core.signing import HEADER, sign
from saas_billing.models import WebhookDelivery, WebhookEndpoint, WebhookEvent
from saas_billing.services.errors import NotFound

log = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def create_endpoint(
    session: Session,
    url: str,
    now: datetime,
    secret: str | None = None,
    description: str | None = None,
) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        url=url, secret=secret or secrets.token_urlsafe(32), description=description, created_at=now
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def list_endpoints(session: Session) -> list[WebhookEndpoint]:
    return list(session.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.created_at)))


def deactivate_endpoint(session: Session, endpoint_id: str) -> WebhookEndpoint:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise NotFound(f"webhook endpoint {endpoint_id} not found")
    endpoint.active = False
    return endpoint


def emit(session: Session, event_type: str, payload: dict[str, Any], now: datetime) -> WebhookEvent:
    event = WebhookEvent(type=event_type, payload=payload, created_at=now)
    session.add(event)
    endpoints = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True)))
    for endpoint in endpoints:
        session.add(WebhookDelivery(event=event, endpoint_id=endpoint.id))
    session.flush()
    return event


def list_events(
    session: Session, limit: int = 100, event_type: str | None = None
) -> list[WebhookEvent]:
    stmt = (
        select(WebhookEvent).order_by(WebhookEvent.created_at.desc(), WebhookEvent.id).limit(limit)
    )
    if event_type:
        stmt = stmt.where(WebhookEvent.type == event_type)
    return list(session.scalars(stmt))


def dispatch(session: Session, http: httpx.Client, now: datetime) -> int:
    """Send every pending delivery once. Returns the number delivered in this pass."""
    pending = session.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.status == "pending")
        .order_by(WebhookDelivery.id)
    ).all()
    delivered = 0
    for delivery in pending:
        event = delivery.event
        body = json.dumps(
            {
                "id": event.id,
                "type": event.type,
                "created_at": iso(event.created_at),
                "data": event.payload,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = int(now.timestamp())
        headers = {
            "Content-Type": "application/json",
            HEADER: sign(delivery.endpoint.secret, timestamp, body),
            "X-Billing-Event": event.type,
            "X-Billing-Event-Id": event.id,
        }
        delivery.attempts += 1
        try:
            response = http.post(delivery.endpoint.url, content=body, headers=headers)
            delivery.last_status_code = response.status_code
            if 200 <= response.status_code < 300:
                delivery.status = "delivered"
                delivery.delivered_at = now
                delivered += 1
                continue
            delivery.last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
        if delivery.attempts >= MAX_DELIVERY_ATTEMPTS:
            delivery.status = "failed"
            log.warning(
                "webhook %s to %s gave up: %s", event.id, delivery.endpoint.url, delivery.last_error
            )
    session.flush()
    return delivered
