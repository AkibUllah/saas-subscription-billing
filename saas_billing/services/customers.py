from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_billing.models import Customer
from saas_billing.services import webhooks
from saas_billing.services.errors import Conflict, NotFound


def create_customer(
    session: Session,
    *,
    name: str,
    email: str,
    now: datetime,
    currency: str = "BDT",
    payment_token: str | None = None,
    tax_exempt: bool = False,
) -> Customer:
    email = email.strip().lower()
    if session.scalar(select(Customer).where(Customer.email == email)):
        raise Conflict(f"customer with email {email} already exists", code="duplicate_email")
    customer = Customer(
        name=name.strip(),
        email=email,
        currency=currency.upper(),
        payment_token=payment_token,
        tax_exempt=tax_exempt,
        created_at=now,
    )
    session.add(customer)
    session.flush()
    webhooks.emit(
        session,
        "customer.created",
        {"id": customer.id, "email": customer.email, "currency": customer.currency},
        now,
    )
    return customer


def get_customer(session: Session, customer_id: str) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise NotFound(f"customer {customer_id} not found")
    return customer


def list_customers(session: Session, limit: int = 100, offset: int = 0) -> list[Customer]:
    return list(
        session.scalars(
            select(Customer).order_by(Customer.created_at, Customer.id).limit(limit).offset(offset)
        )
    )


def update_payment_token(
    session: Session, customer: Customer, payment_token: str | None
) -> Customer:
    customer.payment_token = payment_token
    session.flush()
    return customer
