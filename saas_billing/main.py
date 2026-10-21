from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from saas_billing import __version__
from saas_billing.api import billing, customers, invoices, plans, subscriptions, webhooks
from saas_billing.api.deps import get_ctx, get_db, require_api_key
from saas_billing.clock import Clock
from saas_billing.config import Settings, get_settings
from saas_billing.core.states import InvalidTransition
from saas_billing.database import init_db, make_engine, make_session_factory
from saas_billing.gateways.base import PaymentGateway
from saas_billing.logging_config import configure_logging
from saas_billing.schemas import ErrorResponse, HealthOut
from saas_billing.services.context import BillingContext
from saas_billing.services.errors import DomainError


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    gateway: PaymentGateway | None = None,
    transport: httpx.BaseTransport | None = None,
    create_schema: bool | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    ctx = BillingContext.from_settings(settings, clock=clock, gateway=gateway, transport=transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # SQLite (dev/tests) gets its schema created on boot; PostgreSQL relies on Alembic.
        if (
            create_schema
            if create_schema is not None
            else settings.database_url.startswith("sqlite")
        ):
            init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="SaaS Subscription Billing",
        version=__version__,
        description=(
            "Plans, seats, trials, coupons, proration, invoicing, dunning and SaaS metrics."
        ),
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.ctx = ctx

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(InvalidTransition)
    async def _transition_error(_: Request, exc: InvalidTransition) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": {"code": "invalid_transition", "message": str(exc)}}
        )

    protected = [Depends(require_api_key)]
    for router in (
        customers.router,
        plans.router,
        subscriptions.router,
        invoices.router,
        billing.router,
        webhooks.router,
    ):
        app.include_router(router, dependencies=protected)

    @app.get("/health", response_model=HealthOut, tags=["ops"])
    def health(db=Depends(get_db), ctx: BillingContext = Depends(get_ctx)):
        db.execute(text("SELECT 1"))
        return HealthOut(
            status="ok",
            database="ok",
            gateway=ctx.gateway.name,
            clock=ctx.clock.now(),
            version=__version__,
        )

    return app


app = create_app()
