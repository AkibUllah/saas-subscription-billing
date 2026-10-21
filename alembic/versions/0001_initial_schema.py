"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-21 21:26:54.254427

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('billing_runs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('as_of', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('subscriptions_processed', sa.Integer(), nullable=False),
    sa.Column('invoices_created', sa.Integer(), nullable=False),
    sa.Column('payments_succeeded', sa.Integer(), nullable=False),
    sa.Column('payments_failed', sa.Integer(), nullable=False),
    sa.Column('transitions', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('coupons',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('kind', sa.Enum('percent', 'fixed', name='couponkind', native_enum=False, length=10), nullable=False),
    sa.Column('percent_bps', sa.Integer(), nullable=True),
    sa.Column('amount_minor', sa.Integer(), nullable=True),
    sa.Column('duration', sa.Enum('once', 'repeating', 'forever', name='couponduration', native_enum=False, length=10), nullable=False),
    sa.Column('duration_in_months', sa.Integer(), nullable=True),
    sa.Column('max_redemptions', sa.Integer(), nullable=True),
    sa.Column('times_redeemed', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('customers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('payment_token', sa.String(length=100), nullable=True),
    sa.Column('tax_exempt', sa.Boolean(), nullable=False),
    sa.Column('credit_balance_minor', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('invoice_sequences',
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('last_value', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('year')
    )
    op.create_table('plans',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('interval', sa.Enum('month', 'year', name='interval', native_enum=False, length=10), nullable=False),
    sa.Column('interval_count', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('base_amount_minor', sa.Integer(), nullable=False),
    sa.Column('per_seat_amount_minor', sa.Integer(), nullable=False),
    sa.Column('included_seats', sa.Integer(), nullable=False),
    sa.Column('trial_days', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('webhook_endpoints',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('url', sa.String(length=2000), nullable=False),
    sa.Column('secret', sa.String(length=128), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('webhook_events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('type', sa.String(length=60), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_events_type'), ['type'], unique=False)

    op.create_table('plan_meters',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('plan_id', sa.String(length=32), nullable=False),
    sa.Column('metric', sa.String(length=60), nullable=False),
    sa.Column('unit_amount_minor', sa.Integer(), nullable=False),
    sa.Column('included_units', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_id', 'metric', name='uq_plan_meter_metric')
    )
    op.create_table('subscriptions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=32), nullable=False),
    sa.Column('plan_id', sa.String(length=32), nullable=False),
    sa.Column('coupon_id', sa.String(length=32), nullable=True),
    sa.Column('status', sa.Enum('trialing', 'active', 'past_due', 'paused', 'canceled', 'unpaid', name='subscriptionstatus', native_enum=False, length=12), nullable=False),
    sa.Column('seats', sa.Integer(), nullable=False),
    sa.Column('trial_end', sa.DateTime(), nullable=True),
    sa.Column('current_period_start', sa.DateTime(), nullable=False),
    sa.Column('current_period_end', sa.DateTime(), nullable=False),
    sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False),
    sa.Column('canceled_at', sa.DateTime(), nullable=True),
    sa.Column('ended_at', sa.DateTime(), nullable=True),
    sa.Column('paused_at', sa.DateTime(), nullable=True),
    sa.Column('coupon_applications', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['coupon_id'], ['coupons.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscriptions_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index('ix_subscriptions_status_period_end', ['status', 'current_period_end'], unique=False)

    op.create_table('webhook_deliveries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.String(length=32), nullable=False),
    sa.Column('endpoint_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_status_code', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoints.id'], ),
    sa.ForeignKeyConstraint(['event_id'], ['webhook_events.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('webhook_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_endpoint_id'), ['endpoint_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_event_id'), ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_webhook_deliveries_status'), ['status'], unique=False)

    op.create_table('invoices',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('number', sa.String(length=30), nullable=False),
    sa.Column('customer_id', sa.String(length=32), nullable=False),
    sa.Column('subscription_id', sa.String(length=32), nullable=True),
    sa.Column('period_key', sa.String(length=80), nullable=True),
    sa.Column('status', sa.Enum('open', 'paid', 'uncollectible', 'void', name='invoicestatus', native_enum=False, length=15), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('period_start', sa.DateTime(), nullable=False),
    sa.Column('period_end', sa.DateTime(), nullable=False),
    sa.Column('subtotal_minor', sa.Integer(), nullable=False),
    sa.Column('discount_minor', sa.Integer(), nullable=False),
    sa.Column('credit_applied_minor', sa.Integer(), nullable=False),
    sa.Column('tax_minor', sa.Integer(), nullable=False),
    sa.Column('total_minor', sa.Integer(), nullable=False),
    sa.Column('amount_paid_minor', sa.Integer(), nullable=False),
    sa.Column('coupon_code', sa.String(length=60), nullable=True),
    sa.Column('issued_at', sa.DateTime(), nullable=False),
    sa.Column('due_at', sa.DateTime(), nullable=False),
    sa.Column('paid_at', sa.DateTime(), nullable=True),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('next_payment_attempt_at', sa.DateTime(), nullable=True),
    sa.Column('last_payment_error', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number'),
    sa.UniqueConstraint('period_key')
    )
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invoices_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_invoices_next_payment_attempt_at'), ['next_payment_attempt_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_invoices_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_invoices_subscription_id'), ['subscription_id'], unique=False)

    op.create_table('subscription_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('subscription_id', sa.String(length=32), nullable=False),
    sa.Column('from_status', sa.String(length=12), nullable=True),
    sa.Column('to_status', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=60), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('subscription_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscription_events_occurred_at'), ['occurred_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscription_events_subscription_id'), ['subscription_id'], unique=False)

    op.create_table('invoice_lines',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('invoice_id', sa.String(length=32), nullable=True),
    sa.Column('subscription_id', sa.String(length=32), nullable=True),
    sa.Column('type', sa.Enum('plan', 'seats', 'proration_credit', 'proration_charge', 'usage', name='linetype', native_enum=False, length=20), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_amount_minor', sa.Integer(), nullable=False),
    sa.Column('amount_minor', sa.Integer(), nullable=False),
    sa.Column('period_start', sa.DateTime(), nullable=False),
    sa.Column('period_end', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('invoice_lines', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invoice_lines_invoice_id'), ['invoice_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_invoice_lines_subscription_id'), ['subscription_id'], unique=False)

    op.create_table('payment_attempts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('invoice_id', sa.String(length=32), nullable=False),
    sa.Column('amount_minor', sa.Integer(), nullable=False),
    sa.Column('succeeded', sa.Boolean(), nullable=False),
    sa.Column('gateway', sa.String(length=30), nullable=False),
    sa.Column('reference', sa.String(length=100), nullable=True),
    sa.Column('failure_code', sa.String(length=60), nullable=True),
    sa.Column('attempted_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('payment_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_attempts_invoice_id'), ['invoice_id'], unique=False)

    op.create_table('usage_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('subscription_id', sa.String(length=32), nullable=False),
    sa.Column('metric', sa.String(length=60), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=100), nullable=True),
    sa.Column('invoice_id', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('usage_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usage_records_invoice_id'), ['invoice_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_usage_records_subscription_id'), ['subscription_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('usage_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_usage_records_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_usage_records_invoice_id'))

    op.drop_table('usage_records')
    with op.batch_alter_table('payment_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_attempts_invoice_id'))

    op.drop_table('payment_attempts')
    with op.batch_alter_table('invoice_lines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invoice_lines_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_invoice_lines_invoice_id'))

    op.drop_table('invoice_lines')
    with op.batch_alter_table('subscription_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscription_events_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_subscription_events_occurred_at'))

    op.drop_table('subscription_events')
    with op.batch_alter_table('invoices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invoices_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_invoices_status'))
        batch_op.drop_index(batch_op.f('ix_invoices_next_payment_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_invoices_customer_id'))

    op.drop_table('invoices')
    with op.batch_alter_table('webhook_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_status'))
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_event_id'))
        batch_op.drop_index(batch_op.f('ix_webhook_deliveries_endpoint_id'))

    op.drop_table('webhook_deliveries')
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_index('ix_subscriptions_status_period_end')
        batch_op.drop_index(batch_op.f('ix_subscriptions_customer_id'))

    op.drop_table('subscriptions')
    op.drop_table('plan_meters')
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_events_type'))
        batch_op.drop_index(batch_op.f('ix_webhook_events_created_at'))

    op.drop_table('webhook_events')
    op.drop_table('webhook_endpoints')
    op.drop_table('plans')
    op.drop_table('invoice_sequences')
    op.drop_table('customers')
    op.drop_table('coupons')
    op.drop_table('billing_runs')
