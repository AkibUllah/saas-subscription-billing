import httpx
import pytest

from saas_billing.config import Settings
from saas_billing.gateways import (
    BkashLikeGateway,
    GatewayNotConfigured,
    MockGateway,
    PaymentGateway,
    StripeLikeGateway,
    build_gateway,
)


def test_mock_gateway_is_deterministic_and_idempotent():
    gw = MockGateway()
    assert isinstance(gw, PaymentGateway)
    assert gw.charge("tok_ok", 100, "BDT", "k1").succeeded
    assert gw.charge("tok_ok", 100, "BDT", "k1") is gw.charge("tok_ok", 100, "BDT", "k1")
    assert gw.charge("tok_decline", 100, "BDT", "k2").failure_code == "card_declined"
    assert gw.charge(None, 100, "BDT", "k3").failure_code == "no_payment_method"
    flaky = [gw.charge("tok_fail_then_ok:2", 100, "BDT", f"f{i}").succeeded for i in range(4)]
    assert flaky == [False, False, True, True]
    assert len(gw.charges) == 7


def test_stubs_require_configuration():
    with pytest.raises(GatewayNotConfigured):
        StripeLikeGateway(api_key=None, base_url=None).charge("pm_1", 100, "BDT", "k")
    with pytest.raises(GatewayNotConfigured):
        BkashLikeGateway(api_key="k", base_url=None).charge("agr_1", 100, "BDT", "k")


def test_stripe_like_request_shape_and_error_mapping():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = request.read().decode()
        if "pm_bad" in seen["body"]:
            return httpx.Response(
                402,
                json={
                    "error": {
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                        "message": "no",
                    }
                },
            )
        return httpx.Response(200, json={"id": "pi_123", "status": "succeeded"})

    gw = StripeLikeGateway(
        "sk_test_x",
        "https://sandbox.example",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    ok = gw.charge("pm_good", 172_500, "BDT", "inv_1:1")
    assert ok.succeeded and ok.reference == "pi_123"
    assert seen["headers"]["Idempotency-Key"] == "inv_1:1"
    assert "amount=172500" in seen["body"] and "currency=bdt" in seen["body"]
    bad = gw.charge("pm_bad", 100, "BDT", "inv_1:2")
    assert not bad.succeeded and bad.failure_code == "insufficient_funds"


def test_bkash_like_currency_guard_and_success():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        assert '"amount":"1725.00"' in payload
        return httpx.Response(200, json={"transactionStatus": "Completed", "trxID": "TRX9"})

    gw = BkashLikeGateway(
        "app-key",
        "https://sandbox.example",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert gw.charge("agr_1", 172_500, "USD", "k").failure_code == "unsupported_currency"
    assert gw.charge(None, 172_500, "BDT", "k").failure_code == "no_agreement"
    assert gw.charge("agr_1", 172_500, "BDT", "k").reference == "TRX9"


def test_network_errors_become_failed_charges():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    gw = StripeLikeGateway("k", "https://x", http=httpx.Client(transport=httpx.MockTransport(boom)))
    assert gw.charge("pm", 1, "BDT", "k").failure_code == "network_error"


def test_build_gateway_from_settings():
    assert build_gateway(Settings(_env_file=None, payment_gateway="mock")).name == "mock"
    assert (
        build_gateway(Settings(_env_file=None, payment_gateway="stripe_like")).name == "stripe_like"
    )
    assert (
        build_gateway(Settings(_env_file=None, payment_gateway="bkash_like")).name == "bkash_like"
    )
