from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from saas_billing.clock import FixedClock
from saas_billing.config import Settings
from saas_billing.gateways.mock import MockGateway
from saas_billing.main import create_app
from saas_billing.services import customers, plans, subscriptions
from saas_billing.services.billing_run import run_billing

API_KEY = "test-admin-key"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class FakeReceiver:
    """Webhook target behind an httpx.MockTransport."""

    requests: list[httpx.Request] = field(default_factory=list)
    status: int = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": True})


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        api_key=API_KEY,
        vat_rate_bps=1500,
        dunning_schedule_days="1,3,5,7",
        invoice_due_days=7,
        log_level="WARNING",
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def gateway() -> MockGateway:
    return MockGateway()


@pytest.fixture
def receiver() -> FakeReceiver:
    return FakeReceiver()


@pytest.fixture
def app(settings, clock, gateway, receiver):
    return create_app(
        settings, clock=clock, gateway=gateway, transport=httpx.MockTransport(receiver.handler)
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def ctx(app):
    return app.state.ctx


@pytest.fixture
def session(client, app):
    s = app.state.session_factory()
    try:
        yield s
        s.commit()
    finally:
        s.close()


# --- domain helpers -------------------------------------------------------------------------


def make_customer(session, now, token="tok_ok", email="billing@dhakagrocers.example", **kw):
    return customers.create_customer(
        session, name="Dhaka Grocers Ltd", email=email, now=now, payment_token=token, **kw
    )


def make_plan(session, now, code="starter", base=150_000, **kw):
    defaults = dict(name=code.title(), interval="month", base_amount_minor=base)
    defaults.update(kw)
    return plans.create_plan(session, code=code, now=now, **defaults)


def team_plan(session, now, **kw):
    return make_plan(
        session,
        now,
        code="team",
        base=400_000,
        per_seat_amount_minor=80_000,
        included_seats=3,
        meters=[{"metric": "sms", "unit_amount_minor": 50, "included_units": 1000}],
        **kw,
    )


def subscribe(session, ctx, customer, plan, now, **kw):
    return subscriptions.create_subscription(session, ctx, customer, plan, now, **kw)


def run(session, ctx, as_of):
    return run_billing(session, ctx, as_of)
