from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from saas_billing.clock import Clock, SystemClock
from saas_billing.config import Settings
from saas_billing.gateways.base import PaymentGateway, build_gateway


@dataclass
class BillingContext:
    """Everything a service function needs besides the DB session: config, gateway, clock,
    and the HTTP client used for outgoing webhooks (an `httpx.MockTransport` in tests)."""

    settings: Settings
    gateway: PaymentGateway
    clock: Clock = field(default_factory=SystemClock)
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=5))

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        clock: Clock | None = None,
        gateway: PaymentGateway | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> BillingContext:
        http = httpx.Client(timeout=settings.webhook_timeout_seconds, transport=transport)
        return cls(
            settings=settings,
            gateway=gateway or build_gateway(settings, http=http),
            clock=clock or SystemClock(),
            http=http,
        )
