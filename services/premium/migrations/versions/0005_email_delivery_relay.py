"""Add content-free transcript email delivery receipts.

Revision ID: 0005_email_delivery_relay
Revises: 0004_ad_inventory_contract
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_email_delivery_relay"
down_revision: str | None = "0004_ad_inventory_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_delivery_receipts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("client_delivery_id", sa.String(length=40), nullable=False),
        sa.Column("consent_kind", sa.String(length=32), nullable=False),
        sa.Column("content_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_hmac", sa.String(length=64), nullable=False),
        sa.Column("sink", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "consent_kind IN ('subscription_completion', 'manual')",
            name="ck_email_receipts_consent",
        ),
        sa.CheckConstraint("sink = 'dev_maildir'", name="ck_email_receipts_sink"),
        sa.CheckConstraint(
            "state IN ('processing', 'delivered', 'failed')", name="ck_email_receipts_state"
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code = 'delivery_unavailable'",
            name="ck_email_receipts_error",
        ),
        sa.CheckConstraint(
            "(state = 'delivered' AND delivered_at IS NOT NULL AND error_code IS NULL) OR "
            "(state = 'processing' AND delivered_at IS NULL AND error_code IS NULL) OR "
            "(state = 'failed' AND delivered_at IS NULL "
            "AND error_code = 'delivery_unavailable')",
            name="ck_email_receipts_state_fields",
        ),
        sa.CheckConstraint("attempts >= 1", name="ck_email_receipts_attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_delivery_id", name="uq_email_receipts_user_client"),
    )
    op.create_index(
        "ix_email_receipts_state_updated",
        "email_delivery_receipts",
        ["state", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_receipts_state_updated", table_name="email_delivery_receipts")
    op.drop_table("email_delivery_receipts")
