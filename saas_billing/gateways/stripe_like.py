"""STUB: a card gateway shaped like Stripe's PaymentIntents API.

This is **not** an integration with Stripe. It shows where a real card processor plugs in:
the request shape, idempotency header and error mapping. It only talks to `base_url`, which
in this repository is never a live processor.
"""

from __future__ import annotations

import httpx

from saas_billing.gateways.base import ChargeResult, GatewayNotConfigured


class StripeLikeGateway:
    name = "stripe_like"

    def __init__(
        self, api_key: str | None, base_url: str | None, http: httpx.Client | None = None
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.http = http or httpx.Client(timeout=10)

    def charge(
        self, payment_token: str | None, amount_minor: int, currency: str, idempotency_key: str
    ) -> ChargeResult:
        if not self.api_key or not self.base_url:
            raise GatewayNotConfigured("GATEWAY_API_KEY and GATEWAY_BASE_URL are required")
        if not payment_token:
            return ChargeResult(False, failure_code="no_payment_method")
        try:
            response = self.http.post(
                f"{self.base_url}/v1/payment_intents",
                data={
                    "amount": amount_minor,
                    "currency": currency.lower(),
                    "payment_method": payment_token,
                    "confirm": "true",
                },
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Idempotency-Key": idempotency_key,
                },
            )
        except httpx.HTTPError as exc:
            return ChargeResult(False, failure_code="network_error", message=str(exc))
        body = response.json() if response.content else {}
        if response.status_code == 200 and body.get("status") == "succeeded":
            return ChargeResult(True, reference=body.get("id"))
        error = body.get("error", {})
        return ChargeResult(
            False,
            failure_code=error.get("decline_code") or error.get("code") or "gateway_error",
            message=error.get("message"),
        )
