"""STUB: a mobile-wallet gateway shaped like bKash tokenized checkout (agreement-based).

This is **not** a live bKash integration. Recurring bKash charges require a customer
agreement (`agreement_id`) created during onboarding; here the agreement id is the
customer's `payment_token`. The request/response shapes are illustrative only.
"""

from __future__ import annotations

import httpx

from saas_billing.gateways.base import ChargeResult, GatewayNotConfigured


class BkashLikeGateway:
    name = "bkash_like"

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
        if currency.upper() != "BDT":
            return ChargeResult(False, failure_code="unsupported_currency")
        if not payment_token:
            return ChargeResult(False, failure_code="no_agreement")
        major, minor = divmod(amount_minor, 100)
        try:
            response = self.http.post(
                f"{self.base_url}/tokenized/checkout/execute",
                json={
                    "agreementID": payment_token,
                    "amount": f"{major}.{minor:02d}",
                    "currency": "BDT",
                    "intent": "sale",
                    "merchantInvoiceNumber": idempotency_key,
                },
                headers={"Authorization": self.api_key, "X-App-Key": "stub"},
            )
        except httpx.HTTPError as exc:
            return ChargeResult(False, failure_code="network_error", message=str(exc))
        body = response.json() if response.content else {}
        if response.status_code == 200 and body.get("transactionStatus") == "Completed":
            return ChargeResult(True, reference=body.get("trxID"))
        return ChargeResult(
            False,
            failure_code=body.get("errorCode") or "gateway_error",
            message=body.get("errorMessage"),
        )
