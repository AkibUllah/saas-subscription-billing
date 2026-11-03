from tests.test_api import create_customer, create_plan

FEB1 = "2026-02-01T00:00:00Z"


def subscribe(client, auth, plan_code="starter"):
    plan = create_plan(
        client,
        auth,
        code=plan_code,
        name=plan_code.title(),
        base_amount_minor=150_000,
        per_seat_amount_minor=0,
        included_seats=0,
        meters=[],
    )
    customer = create_customer(client, auth)
    r = client.post(
        "/v1/subscriptions",
        json={"customer_id": customer["id"], "plan_id": plan["id"]},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_schedule_list_and_cancel_through_api(client, auth, clock):
    sub = subscribe(client, auth)
    team = create_plan(client, auth)
    base = f"/v1/subscriptions/{sub['id']}/scheduled-changes"

    r = client.post(
        base, json={"action": "change_plan", "plan_id": "team", "seats": 5}, headers=auth
    )
    assert r.status_code == 201, r.text
    change = r.json()
    assert change["status"] == "pending" and change["at_period_end"] is True
    assert change["plan_id"] == team["id"] and change["seats"] == 5
    assert change["effective_at"].startswith("2026-02-01T00:00:00")

    r = client.post(base, json={"action": "change_seats", "seats": 2}, headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflicting_change"
    r = client.post(base, json={"action": "change_plan", "plan_id": "team"}, headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "change_already_scheduled"
    r = client.post(base, json={"action": "resume"}, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "effective_at_required"
    r = client.post(
        base, json={"action": "pause", "effective_at": "2025-12-31T00:00:00Z"}, headers=auth
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_effective_at"
    r = client.post(base, json={"action": "change_seats", "seats": 0}, headers=auth)
    assert r.status_code == 422
    r = client.post(base, json={"action": "explode"}, headers=auth)
    assert r.status_code == 422

    clock.advance(hours=1)
    r = client.post(
        base, json={"action": "pause", "effective_at": "2026-01-20T00:00:00"}, headers=auth
    )
    assert r.status_code == 201 and r.json()["effective_at"].startswith("2026-01-20T00:00:00")
    pause = r.json()

    listed = client.get(base, headers=auth).json()
    assert [c["id"] for c in listed] == [change["id"], pause["id"]]

    r = client.delete(f"{base}/{change['id']}", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    r = client.delete(f"{base}/{change['id']}", headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "change_not_pending"
    assert client.delete(f"{base}/sch_missing", headers=auth).status_code == 404
    pending = client.get(base, params={"status": "pending"}, headers=auth).json()
    assert [c["id"] for c in pending] == [pause["id"]]
    assert (
        client.get("/v1/subscriptions/sub_missing/scheduled-changes", headers=auth).status_code
        == 404
    )
    assert client.get(base).status_code == 401


def test_scheduled_downgrade_applied_by_billing_run_endpoint(client, auth):
    sub = subscribe(client, auth, plan_code="pro")
    create_plan(
        client,
        auth,
        code="basic",
        name="Basic",
        base_amount_minor=90_000,
        per_seat_amount_minor=0,
        included_seats=0,
        meters=[],
    )
    base = f"/v1/subscriptions/{sub['id']}/scheduled-changes"
    r = client.post(base, json={"action": "change_plan", "plan_id": "basic"}, headers=auth)
    assert r.status_code == 201
    r = client.post("/v1/billing/run", params={"as_of": FEB1}, headers=auth)
    assert r.status_code == 200
    assert r.json()["transitions"]["scheduled_change_plan"] == 1
    (change,) = client.get(base, headers=auth).json()
    assert change["status"] == "applied" and change["applied_at"].startswith("2026-02-01")
    invoices = client.get(
        "/v1/invoices", params={"subscription_id": sub["id"]}, headers=auth
    ).json()
    renewal = max(invoices, key=lambda inv: inv["number"])
    assert renewal["subtotal_minor"] == 90_000
    assert {line["type"] for line in renewal["lines"]} == {"plan"}
