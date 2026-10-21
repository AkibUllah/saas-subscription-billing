from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from saas_billing.clock import ensure_utc
from saas_billing.config import Settings
from saas_billing.services.context import BillingContext


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_ctx(request: Request) -> BillingContext:
    return request.app.state.ctx


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def now(ctx: BillingContext = Depends(get_ctx)) -> datetime:
    return ensure_utc(ctx.clock.now())


def require_api_key(
    settings: Settings = Depends(get_settings), x_api_key: str | None = Header(default=None)
) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
