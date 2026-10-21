from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from saas_billing.api.deps import get_db, now
from saas_billing.schemas import CustomerCreate, CustomerOut, CustomerUpdate
from saas_billing.services import customers

router = APIRouter(prefix="/v1/customers", tags=["customers"])


@router.post("", response_model=CustomerOut, status_code=201)
def create(body: CustomerCreate, db: Session = Depends(get_db), at: datetime = Depends(now)):
    return customers.create_customer(db, now=at, **body.model_dump())


@router.get("", response_model=list[CustomerOut])
def list_(db: Session = Depends(get_db), limit: int = Query(100, le=500), offset: int = 0):
    return customers.list_customers(db, limit=limit, offset=offset)


@router.get("/{customer_id}", response_model=CustomerOut)
def get(customer_id: str, db: Session = Depends(get_db)):
    return customers.get_customer(db, customer_id)


@router.patch("/{customer_id}", response_model=CustomerOut)
def update(customer_id: str, body: CustomerUpdate, db: Session = Depends(get_db)):
    customer = customers.get_customer(db, customer_id)
    return customers.update_payment_token(db, customer, body.payment_token)
