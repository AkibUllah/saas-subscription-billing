from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from saas_billing.api.deps import get_ctx, get_db, now
from saas_billing.models import InvoiceStatus
from saas_billing.schemas import InvoiceOut
from saas_billing.services import invoicing, payments, webhooks
from saas_billing.services.context import BillingContext

router = APIRouter(prefix="/v1/invoices", tags=["invoices"])


@router.get("", response_model=list[InvoiceOut])
def list_(
    response: Response,
    db: Session = Depends(get_db),
    customer_id: str | None = None,
    subscription_id: str | None = None,
    status: InvoiceStatus | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
):
    rows, total = invoicing.list_invoices(
        db,
        customer_id=customer_id,
        subscription_id=subscription_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get(invoice_id: str, db: Session = Depends(get_db)):
    return invoicing.get_invoice(db, invoice_id)


@router.get("/{invoice_id}/html", response_class=HTMLResponse)
def html(invoice_id: str, db: Session = Depends(get_db), ctx: BillingContext = Depends(get_ctx)):
    invoice = invoicing.get_invoice(db, invoice_id)
    return invoicing.render_invoice_html(invoice, ctx.settings.vat_rate_bps)


@router.post("/{invoice_id}/pay", response_model=InvoiceOut)
def pay(
    invoice_id: str,
    db: Session = Depends(get_db),
    ctx: BillingContext = Depends(get_ctx),
    at: datetime = Depends(now),
):
    """Retry collection now (e.g. after the customer updated their card)."""
    invoice = invoicing.get_invoice(db, invoice_id)
    payments.collect(db, ctx, invoice, at)
    webhooks.dispatch(db, ctx.http, at)
    return invoice
