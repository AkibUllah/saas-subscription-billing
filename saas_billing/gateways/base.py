from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import httpx

    from saas_billing.config import Settings


@dataclass(frozen=True)
class ChargeResult:
    succeeded: bool
    reference: str | None = None
    failure_code: str | None = None
    message: str | None = None


class GatewayNotConfigured(RuntimeError):
    pass


@runtime_checkable
class PaymentGateway(Protocol):
    name: str

    def charge(
        self, payment_token: str | None, amount_minor: int, currency: str, idempotency_key: str
    ) -> ChargeResult: ...


def build_gateway(settings: Settings, http: httpx.Client | None = None) -> PaymentGateway:
    from saas_billing.gateways.bkash_like import BkashLikeGateway
    from saas_billing.gateways.mock import MockGateway
    from saas_billing.gateways.stripe_like import StripeLikeGateway

    if settings.payment_gateway == "stripe_like":
        return StripeLikeGateway(
            api_key=settings.gateway_api_key, base_url=settings.gateway_base_url, http=http
        )
    if settings.payment_gateway == "bkash_like":
        return BkashLikeGateway(
            api_key=settings.gateway_api_key, base_url=settings.gateway_base_url, http=http
        )
    return MockGateway()
