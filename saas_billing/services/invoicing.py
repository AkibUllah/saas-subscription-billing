"""Invoice construction: recurring lines, metered usage, pending proration items, coupons,
customer credit and VAT, in that order."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saas_billing.core.coupons import discount_amount, still_applies
from saas_billing.core.money import format_money, percent_of
from saas_billing.models import (
    Customer,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    InvoiceStatus,
    LineType,
    Subscription,
    UsageRecord,
)
from saas_billing.services import webhooks
from saas_billing.services.context import BillingContext
from saas_billing.services.errors import NotFound
from saas_billing.services.webhooks import iso

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html"]))
_env.filters["money"] = format_money


def period_label(start: datetime, end: datetime) -> str:
    return f"{start:%d %b %Y} – {(end - timedelta(seconds=1)):%d %b %Y}"


def next_invoice_number(session: Session, issued_at: datetime, prefix: str) -> str:
    seq = session.get(InvoiceSequence, issued_at.year, with_for_update=True)
    if seq is None:
        seq = InvoiceSequence(year=issued_at.year, last_value=0)
        session.add(seq)
    seq.last_value += 1
    return f"{prefix}-{issued_at.year}-{seq.last_value:06d}"


def recurring_lines(
    sub: Subscription, period_start: datetime, period_end: datetime, now: datetime
) -> list[InvoiceLine]:
    plan = sub.plan
    label = period_label(period_start, period_end)
    lines = [
        InvoiceLine(
            subscription_id=sub.id,
            type=LineType.PLAN,
            description=f"{plan.name} ({label})",
            quantity=1,
            unit_amount_minor=plan.base_amount_minor,
            amount_minor=plan.base_amount_minor,
            period_start=period_start,
            period_end=period_end,
            created_at=now,
        )
    ]
    billable_seats = max(0, sub.seats - plan.included_seats)
    if billable_seats and plan.per_seat_amount_minor:
        lines.append(
            InvoiceLine(
                subscription_id=sub.id,
                type=LineType.SEATS,
                description=(
                    f"{billable_seats} additional seat(s) × "
                    f"{format_money(plan.per_seat_amount_minor, plan.currency)}"
                ),
                quantity=billable_seats,
                unit_amount_minor=plan.per_seat_amount_minor,
                amount_minor=billable_seats * plan.per_seat_amount_minor,
                period_start=period_start,
                period_end=period_end,
                created_at=now,
            )
        )
    return lines


def usage_lines(
    session: Session, sub: Subscription, period_start: datetime, period_end: datetime, now: datetime
) -> tuple[list[InvoiceLine], list[UsageRecord]]:
    """Aggregate unbilled usage recorded in [period_start, period_end) per metered add-on."""
    records = session.scalars(
        select(UsageRecord).where(
            UsageRecord.subscription_id == sub.id,
            UsageRecord.invoice_id.is_(None),
            UsageRecord.recorded_at >= period_start,
            UsageRecord.recorded_at < period_end,
        )
    ).all()
    totals: dict[str, int] = {}
    for record in records:
        totals[record.metric] = totals.get(record.metric, 0) + record.quantity
    lines: list[InvoiceLine] = []
    for meter in sub.plan.meters:
        used = totals.get(meter.metric, 0)
        billable = max(0, used - meter.included_units)
        if billable <= 0:
            continue
        lines.append(
            InvoiceLine(
                subscription_id=sub.id,
                type=LineType.USAGE,
                description=(
                    f"{meter.metric}: {used:,} used, {meter.included_units:,} included, "
                    f"{billable:,} × {format_money(meter.unit_amount_minor, sub.plan.currency)}"
                ),
                quantity=billable,
                unit_amount_minor=meter.unit_amount_minor,
                amount_minor=billable * meter.unit_amount_minor,
                period_start=period_start,
                period_end=period_end,
                created_at=now,
            )
        )
    return lines, list(records)


def pending_lines(session: Session, sub: Subscription) -> list[InvoiceLine]:
    return list(
        session.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.subscription_id == sub.id, InvoiceLine.invoice_id.is_(None))
            .order_by(InvoiceLine.id)
        )
    )


def finalize_invoice(
    session: Session,
    ctx: BillingContext,
    customer: Customer,
    sub: Subscription | None,
    lines: list[InvoiceLine],
    *,
    period_start: datetime,
    period_end: datetime,
    issued_at: datetime,
    period_key: str | None,
    usage_records: list[UsageRecord] | None = None,
) -> Invoice:
    settings = ctx.settings
    subtotal = sum(line.amount_minor for line in lines)

    discount = 0
    coupon_code = None
    coupon = sub.coupon if sub is not None else None
    if coupon is not None and coupon.active and subtotal > 0:
        if still_applies(
            coupon.duration,
            coupon.duration_in_months,
            sub.plan.months_per_period,
            sub.coupon_applications,
        ):
            discount = discount_amount(
                subtotal, coupon.kind, coupon.percent_bps, coupon.amount_minor
            )
            coupon_code = coupon.code
            sub.coupon_applications += 1

    after_discount = subtotal - discount
    if after_discount < 0:
        # More credit than charges: carry the surplus on the customer's balance.
        customer.credit_balance_minor += -after_discount
        after_discount = 0

    tax = 0 if customer.tax_exempt else percent_of(after_discount, settings.vat_rate_bps)
    gross = after_discount + tax
    credit_applied = min(customer.credit_balance_minor, gross)
    customer.credit_balance_minor -= credit_applied
    total = gross - credit_applied

    invoice = Invoice(
        number=next_invoice_number(session, issued_at, settings.invoice_number_prefix),
        customer_id=customer.id,
        subscription_id=sub.id if sub is not None else None,
        period_key=period_key,
        status=InvoiceStatus.OPEN,
        currency=customer.currency,
        period_start=period_start,
        period_end=period_end,
        subtotal_minor=subtotal,
        discount_minor=discount,
        credit_applied_minor=credit_applied,
        tax_minor=tax,
        total_minor=total,
        coupon_code=coupon_code,
        issued_at=issued_at,
        due_at=issued_at + timedelta(days=settings.invoice_due_days),
    )
    session.add(invoice)
    for line in lines:
        line.invoice = invoice
        session.add(line)
    session.flush()
    for record in usage_records or []:
        record.invoice_id = invoice.id
    webhooks.emit(session, "invoice.created", invoice_payload(invoice), issued_at)
    return invoice


def invoice_payload(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "number": invoice.number,
        "customer_id": invoice.customer_id,
        "subscription_id": invoice.subscription_id,
        "status": invoice.status.value,
        "currency": invoice.currency,
        "total_minor": invoice.total_minor,
        "amount_due_minor": invoice.amount_due_minor,
        "period_start": iso(invoice.period_start),
        "period_end": iso(invoice.period_end),
        "attempt_count": invoice.attempt_count,
        "next_payment_attempt_at": iso(invoice.next_payment_attempt_at),
    }


def get_invoice(session: Session, invoice_id: str) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        invoice = session.scalar(select(Invoice).where(Invoice.number == invoice_id))
    if invoice is None:
        raise NotFound(f"invoice {invoice_id} not found")
    return invoice


def list_invoices(
    session: Session,
    *,
    customer_id: str | None = None,
    subscription_id: str | None = None,
    status: InvoiceStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Invoice], int]:
    stmt = select(Invoice)
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if subscription_id:
        stmt = stmt.where(Invoice.subscription_id == subscription_id)
    if status:
        stmt = stmt.where(Invoice.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(Invoice.issued_at.desc(), Invoice.number.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def render_invoice_html(invoice: Invoice, vat_rate_bps: int) -> str:
    template = _env.get_template("invoice.html")
    return template.render(
        invoice=invoice, customer=invoice.customer, vat_percent=vat_rate_bps / 100
    )
