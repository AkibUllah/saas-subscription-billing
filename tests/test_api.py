import json
from datetime import timedelta

from saas_billing.core.signing import HEADER, verify
from tests.conftest import T0


def create_plan(client, auth, **overrides):
    body = {
        "code": "team",
        "name": "Team",
        "interval": "month",
        "base_amount_minor": 400_000,
        "per_seat_amount_minor": 80_000,
        "included_seats": 3,
        "meters": [{"metric": "sms", "unit_amount_minor": 50, "included_units": 1000}],
    }
    body.update(overrides)
    r = client.post("/v1/plans", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def create_customer(client, auth, **overrides):
    body = {
        "name": "Dhaka Grocers Ltd",
        "email": "billing@dhakagrocers.example",
        "payment_token": "tok_ok",
    }
    body.update(overrides)
    r = client.post("/v1/customers", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_auth(client, auth):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["gateway"] == "mock" and r.json()["clock"].startswith("2026-01-01")
    assert client.get("/v1/plans").status_code == 401
    assert client.get("/v1/plans", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/v1/plans", headers=auth).status_code == 200


def test_customer_crud_and_duplicate_email(client, auth):
    cust = create_customer(client, auth)
    assert cust["currency"] == "BDT" and cust["credit_balance_minor"] == 0
    r = client.post(
        "/v1/customers", json={"name": "Dup", "email": "BILLING@dhakagrocers.example"}, headers=auth
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_email"
    r = client.patch(
        f"/v1/customers/{cust['id']}", json={"payment_token": "tok_decline"}, headers=auth
    )
    assert r.json()["payment_token"] == "tok_decline"
    assert client.get("/v1/customers/cus_missing", headers=auth).status_code == 404
    assert (
        client.post(
            "/v1/customers", json={"name": "x", "email": "not-an-email"}, headers=auth
        ).status_code
        == 422
    )


def test_plan_lookup_by_code_and_coupon_validation(client, auth):
    plan = create_plan(client, auth)
    assert client.get("/v1/plans/team", headers=auth).json()["id"] == plan["id"]
    assert client.get("/v1/plans/team", headers=auth).json()["meters"][0]["metric"] == "sms"
    r = client.post(
        "/v1/coupons", json={"code": "BAD", "kind": "percent", "duration": "once"}, headers=auth
    )
    assert r.status_code == 422
    r = client.post(
        "/v1/coupons",
        json={
            "code": "LAUNCH20",
            "kind": "percent",
            "percent_bps": 2000,
            "duration": "repeating",
            "duration_in_months": 3,
        },
        headers=auth,
    )
    assert r.status_code == 201 and r.json()["times_redeemed"] == 0
    client.delete(f"/v1/plans/{plan['id']}", headers=auth)
    assert client.get("/v1/plans", headers=auth).json() == []
    assert len(client.get("/v1/plans?include_inactive=true", headers=auth).json()) == 1


def test_subscription_flow_through_api(client, auth, clock):
    plan = create_plan(client, auth)
    cust = create_customer(client, auth)
    r = client.post(
        "/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": "team", "seats": 5},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["status"] == "active" and sub["plan"]["code"] == "team"
    assert sub["events"][0]["reason"] == "created"

    invoices = client.get(f"/v1/invoices?customer_id={cust['id']}", headers=auth)
    assert invoices.headers["X-Total-Count"] == "1"
    inv = invoices.json()[0]
    assert inv["status"] == "paid" and inv["total_minor"] == 644_000
    assert [ln["type"] for ln in inv["lines"]] == ["plan", "seats"]
    html = client.get(f"/v1/invoices/{inv['number']}/html", headers=auth)
    assert html.status_code == 200 and "৳6,440.00" in html.text and cust["name"] in html.text

    clock.advance(days=15, hours=12)
    r = client.post(
        f"/v1/subscriptions/{sub['id']}/seats", json={"seats": 8, "invoice_now": True}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.json()["subscription"]["seats"] == 8
    assert r.json()["invoice"]["lines"][0]["amount_minor"] == 120_000

    r = client.post(
        f"/v1/subscriptions/{sub['id']}/usage",
        json={"metric": "sms", "quantity": 1500, "idempotency_key": "u1"},
        headers=auth,
    )
    assert r.status_code == 201
    r = client.post(
        f"/v1/subscriptions/{sub['id']}/change-plan",
        json={"plan_id": plan["id"], "seats": 8},
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["invoice"] is None  # no-op change

    r = client.post(f"/v1/subscriptions/{sub['id']}/pause", headers=auth)
    assert r.json()["status"] == "paused"
    r = client.post(f"/v1/subscriptions/{sub['id']}/pause", headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_active"
    assert (
        client.post(f"/v1/subscriptions/{sub['id']}/resume", headers=auth).json()["status"]
        == "active"
    )
    r = client.post(
        f"/v1/subscriptions/{sub['id']}/cancel", json={"at_period_end": True}, headers=auth
    )
    assert r.json()["cancel_at_period_end"] is True
    assert (
        client.post(f"/v1/subscriptions/{sub['id']}/reactivate", headers=auth).json()[
            "cancel_at_period_end"
        ]
        is False
    )
    listing = client.get("/v1/subscriptions?status=active", headers=auth)
    assert listing.headers["X-Total-Count"] == "1"


def test_billing_run_and_metrics_endpoints(client, auth):
    create_plan(
        client,
        auth,
        code="starter",
        base_amount_minor=150_000,
        per_seat_amount_minor=0,
        included_seats=0,
        meters=[],
        trial_days=14,
    )
    cust = create_customer(client, auth)
    client.post(
        "/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": "starter"}, headers=auth
    )
    as_of = (T0 + timedelta(days=14)).isoformat()
    r = client.post("/v1/billing/run", params={"as_of": as_of}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["invoices_created"] == 1 and r.json()["transitions"] == {"trial_converted": 1}
    assert client.get("/v1/billing/runs", headers=auth).json()[0]["id"] == r.json()["id"]
    m = client.get("/v1/metrics", params={"as_of": as_of, "window_days": 30}, headers=auth).json()
    assert (
        m["mrr_minor"] == 150_000 and m["trials_converted"] == 1 and m["active_subscriptions"] == 1
    )


def test_manual_invoice_pay_and_closed_invoice_error(client, auth):
    create_plan(
        client,
        auth,
        code="starter",
        base_amount_minor=150_000,
        per_seat_amount_minor=0,
        included_seats=0,
        meters=[],
    )
    cust = create_customer(client, auth, payment_token="tok_decline")
    client.post(
        "/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": "starter"}, headers=auth
    )
    inv = client.get("/v1/invoices?status=open", headers=auth).json()[0]
    assert inv["attempt_count"] == 1 and inv["last_payment_error"] == "card_declined"
    client.patch(f"/v1/customers/{cust['id']}", json={"payment_token": "tok_ok"}, headers=auth)
    r = client.post(f"/v1/invoices/{inv['id']}/pay", headers=auth)
    assert r.json()["status"] == "paid" and r.json()["attempt_count"] == 2
    r = client.post(f"/v1/invoices/{inv['id']}/pay", headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invoice_closed"


def test_invalid_transition_is_409(client, auth):
    create_plan(
        client,
        auth,
        code="starter",
        base_amount_minor=150_000,
        per_seat_amount_minor=0,
        included_seats=0,
        meters=[],
    )
    cust = create_customer(client, auth)
    sub = client.post(
        "/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": "starter"}, headers=auth
    ).json()
    client.post(
        f"/v1/subscriptions/{sub['id']}/cancel", json={"at_period_end": False}, headers=auth
    )
    r = client.post(
        f"/v1/subscriptions/{sub['id']}/cancel", json={"at_period_end": False}, headers=auth
    )
    assert r.status_code == 400  # DomainError: already canceled
    r = client.post(
        f"/v1/subscriptions/{sub['id']}/usage", json={"metric": "sms", "quantity": 1}, headers=auth
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "subscription_not_billable"


def test_webhooks_are_signed_and_delivered(client, auth, receiver):
    r = client.post(
        "/v1/webhooks/endpoints",
        json={"url": "https://shop.example.com/billing-hook", "secret": "s3cr3t-s3cr3t-s3cr3t"},
        headers=auth,
    )
    assert r.status_code == 201 and r.json()["secret"] == "s3cr3t-s3cr3t-s3cr3t"
    assert "secret" not in client.get("/v1/webhooks/endpoints", headers=auth).json()[0]
    create_plan(
        client,
        auth,
        code="starter",
        base_amount_minor=150_000,
        per_seat_amount_minor=0,
        included_seats=0,
        meters=[],
    )
    cust = create_customer(client, auth)
    client.post(
        "/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": "starter"}, headers=auth
    )

    types = [req.headers["X-Billing-Event"] for req in receiver.requests]
    assert types == ["customer.created", "subscription.created", "invoice.created", "invoice.paid"]
    req = receiver.requests[-1]
    verify("s3cr3t-s3cr3t-s3cr3t", req.content, req.headers[HEADER], now=int(T0.timestamp()))
    body = json.loads(req.content)
    assert body["type"] == "invoice.paid" and body["data"]["status"] == "paid"

    events = client.get("/v1/webhooks/events?type=invoice.paid", headers=auth).json()
    assert len(events) == 1 and events[0]["deliveries"][0]["status"] == "delivered"
    assert (
        client.delete(f"/v1/webhooks/endpoints/{r.json()['id']}", headers=auth).json()["active"]
        is False
    )
    assert client.post("/v1/webhooks/dispatch", headers=auth).json() == {"delivered": 0}


def test_failed_webhook_delivery_is_retried_then_marked_failed(client, auth, receiver):
    receiver.status = 500
    client.post(
        "/v1/webhooks/endpoints", json={"url": "https://down.example.com/hook"}, headers=auth
    )
    create_customer(client, auth)
    for _ in range(3):
        assert client.post("/v1/webhooks/dispatch", headers=auth).json()["delivered"] == 0
    delivery = client.get("/v1/webhooks/events", headers=auth).json()[0]["deliveries"][0]
    assert (
        delivery["status"] == "failed"
        and delivery["attempts"] == 3
        and delivery["last_status_code"] == 500
    )
    receiver.status = 200
    assert (
        client.post("/v1/webhooks/dispatch", headers=auth).json()["delivered"] == 0
    )  # failed is terminal
