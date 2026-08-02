"""Create the service/auth foundation.

Revision ID: 0001_auth_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_auth_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("verification", sa.String(32), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    )
    op.create_table(
        "browser_sessions",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("csrf_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_browser_sessions_user_id", "browser_sessions", ["user_id"])
    op.create_table(
        "device_authorizations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("device_code_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("client_kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("approving_user_id", sa.String(40)),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("poll_count", sa.Integer(), nullable=False),
        sa.Column("last_polled_at", sa.Integer()),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["approving_user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "token_families",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("client_kind", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(40), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("used_at", sa.Integer()),
        sa.Column("replacement_digest", sa.String(64)),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["token_families.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "access_tokens",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["token_families.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_access_tokens_family_id", "access_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_access_tokens_family_id", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("token_families")
    op.drop_table("device_authorizations")
    op.drop_index("ix_browser_sessions_user_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
    op.drop_table("users")
