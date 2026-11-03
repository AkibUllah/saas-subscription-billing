"""scheduled changes

Pending pause / resume / plan / seat changes applied by the billing run. The partial unique
index allows one pending change per action and subscription.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03 19:05:27.349422

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('scheduled_changes',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('subscription_id', sa.String(length=32), nullable=False),
    sa.Column('action', sa.Enum('pause', 'resume', 'change_plan', 'change_seats', name='scheduledaction', native_enum=False, length=15), nullable=False),
    sa.Column('status', sa.Enum('pending', 'applied', 'canceled', 'failed', name='scheduledchangestatus', native_enum=False, length=10), nullable=False),
    sa.Column('plan_id', sa.String(length=32), nullable=True),
    sa.Column('seats', sa.Integer(), nullable=True),
    sa.Column('scheduled_for', sa.DateTime(), nullable=True),
    sa.Column('applied_at', sa.DateTime(), nullable=True),
    sa.Column('canceled_at', sa.DateTime(), nullable=True),
    sa.Column('failure_reason', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('scheduled_changes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_scheduled_changes_scheduled_for'), ['scheduled_for'], unique=False)
        batch_op.create_index(batch_op.f('ix_scheduled_changes_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_scheduled_changes_subscription_id'), ['subscription_id'], unique=False)
        batch_op.create_index('uq_scheduled_changes_pending_action', ['subscription_id', 'action'], unique=True, sqlite_where=sa.text("status = 'pending'"), postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    with op.batch_alter_table('scheduled_changes', schema=None) as batch_op:
        batch_op.drop_index('uq_scheduled_changes_pending_action')
        batch_op.drop_index(batch_op.f('ix_scheduled_changes_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_scheduled_changes_status'))
        batch_op.drop_index(batch_op.f('ix_scheduled_changes_scheduled_for'))

    op.drop_table('scheduled_changes')
