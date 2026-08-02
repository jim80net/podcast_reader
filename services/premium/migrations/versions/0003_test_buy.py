"""Add test Checkout and durable payment inbox state.

Revision ID: 0003_test_buy
Revises: 0002_entitlements_admin
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_test_buy"
down_revision: str | None = "0002_entitlements_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stripe_customers",
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("customer_id"),
    )
    op.create_table(
        "checkout_attempts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("checkout_session_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('created', 'session_created', 'completed', 'expired', 'failed')",
            name="ck_checkout_attempts_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_session_id"),
    )
    op.create_index(
        "ix_checkout_attempts_user_created", "checkout_attempts", ["user_id", "created_at"]
    )
    op.create_table(
        "payment_events",
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=128), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("received_at", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.Integer(), nullable=True),
        sa.Column("retry_at", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("processed_at", sa.Integer(), nullable=True),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'processed', 'rejected', 'parked')",
            name="ck_payment_events_state",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_payment_events_attempts"),
        sa.PrimaryKeyConstraint("provider_event_id"),
    )
    op.create_index(
        "ix_payment_events_state_retry",
        "payment_events",
        ["state", "retry_at", "received_at"],
    )
    op.create_index(
        "uq_entitlement_events_source_reference",
        "entitlement_events",
        ["source_reference"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_entitlement_events_source_reference", table_name="entitlement_events")
    op.drop_index("ix_payment_events_state_retry", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index("ix_checkout_attempts_user_created", table_name="checkout_attempts")
    op.drop_table("checkout_attempts")
    op.drop_table("stripe_customers")
