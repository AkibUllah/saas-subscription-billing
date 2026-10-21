"""Deterministic in-memory gateway for development and tests.

Behaviour is decided by the payment token:

- `tok_ok` / anything else           -> succeeds
- `tok_decline`                      -> always fails with `card_declined`
- `tok_insufficient`                 -> always fails with `insufficient_funds`
- `tok_fail_then_ok:<n>`             -> fails the first *n* distinct attempts, then succeeds
- missing token                      -> fails with `no_payment_method`

Charges are idempotent on `idempotency_key`: repeating a key returns the stored result.
"""

from __future__ import annotations

from saas_billing.gateways.base import ChargeResult


class MockGateway:
    name = "mock"

    def __init__(self) -> None:
        self.charges: list[dict] = []
        self._by_key: dict[str, ChargeResult] = {}
        self._failures_by_token: dict[str, int] = {}

    def charge(
        self, payment_token: str | None, amount_minor: int, currency: str, idempotency_key: str
    ) -> ChargeResult:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        result = self._decide(payment_token)
        self._by_key[idempotency_key] = result
        self.charges.append(
            {
                "token": payment_token,
                "amount_minor": amount_minor,
                "currency": currency,
                "idempotency_key": idempotency_key,
                "succeeded": result.succeeded,
            }
        )
        return result

    def _decide(self, token: str | None) -> ChargeResult:
        if not token:
            return ChargeResult(False, failure_code="no_payment_method", message="no card on file")
        if token == "tok_decline":
            return ChargeResult(False, failure_code="card_declined", message="card declined")
        if token == "tok_insufficient":
            return ChargeResult(
                False, failure_code="insufficient_funds", message="insufficient funds"
            )
        if token.startswith("tok_fail_then_ok:"):
            limit = int(token.split(":", 1)[1])
            seen = self._failures_by_token.get(token, 0)
            if seen < limit:
                self._failures_by_token[token] = seen + 1
                return ChargeResult(False, failure_code="try_again_later", message="issuer busy")
        return ChargeResult(True, reference=f"mock_{len(self.charges) + 1:06d}")
