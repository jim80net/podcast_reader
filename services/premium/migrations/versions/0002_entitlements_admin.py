"""Add entitlement, feature, house-ad, and audit state.

Revision ID: 0002_entitlements_admin
Revises: 0001_auth_foundation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_entitlements_admin"
down_revision: str | None = "0001_auth_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entitlement_events",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=True),
        sa.Column("source_reference", sa.String(length=128), nullable=True),
        sa.Column("actor_user_id", sa.String(length=40), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('provider_grant', 'provider_revoke', 'override_set', 'override_clear')",
            name="ck_entitlement_events_type",
        ),
        sa.CheckConstraint(
            "tier IS NULL OR tier IN ('free', 'premium')", name="ck_entitlement_events_tier"
        ),
        sa.CheckConstraint(
            "(event_type = 'provider_grant' AND tier IS NOT NULL AND tier = 'premium') OR "
            "(event_type = 'provider_revoke' AND (tier IS NULL OR tier = 'free')) OR "
            "(event_type = 'override_set' AND tier IS NOT NULL "
            "AND tier IN ('free', 'premium')) OR "
            "(event_type = 'override_clear' AND tier IS NULL)",
            name="ck_entitlement_events_type_tier",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_entitlement_events_revision"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "revision", name="uq_entitlement_events_user_revision"),
    )
    op.create_index(
        "ix_entitlement_events_user_created",
        "entitlement_events",
        ["user_id", "created_at"],
    )
    op.create_table(
        "entitlement_projection",
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("provider_tier", sa.String(length=16), nullable=False),
        sa.Column("provider_source", sa.String(length=32), nullable=False),
        sa.Column("admin_override", sa.String(length=16), nullable=True),
        sa.Column("effective_tier", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "admin_override IS NULL OR admin_override IN ('free', 'premium')",
            name="ck_projection_admin_override",
        ),
        sa.CheckConstraint("effective_tier IN ('free', 'premium')", name="ck_projection_effective"),
        sa.CheckConstraint(
            "provider_source IN ('none', 'test_purchase')",
            name="ck_projection_provider_source",
        ),
        sa.CheckConstraint(
            "provider_tier IN ('free', 'premium')", name="ck_projection_provider_tier"
        ),
        sa.CheckConstraint("revision >= 0", name="ck_projection_revision"),
        sa.ForeignKeyConstraint(["last_event_id"], ["entitlement_events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.String(length=2048), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "audience IN ('off', 'all', 'free', 'premium')", name="ck_flags_audience"
        ),
        sa.CheckConstraint("revision >= 0", name="ck_flags_revision"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "ad_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("enabled_slots_json", sa.String(length=512), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ad_config_singleton"),
        sa.CheckConstraint("revision >= 0", name="ck_ad_config_revision"),
        sa.CheckConstraint("source = 'house'", name="ck_ad_config_house_only"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "house_ads",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("cta_url", sa.String(length=2048), nullable=False),
        sa.Column("starts_at", sa.Integer(), nullable=True),
        sa.Column("ends_at", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_house_ads_schedule",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_house_ads_revision"),
        sa.CheckConstraint("status IN ('draft', 'active')", name="ck_house_ads_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", sa.String(length=40), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("before_digest", sa.String(length=64), nullable=False),
        sa.Column("after_digest", sa.String(length=64), nullable=False),
        sa.Column("delta_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("request_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_action_created", "audit_log", ["action", "created_at"])
    op.create_index("ix_audit_actor_created", "audit_log", ["actor_user_id", "created_at"])
    op.create_index(
        "ix_audit_target_created", "audit_log", ["target_kind", "target_id", "created_at"]
    )
    op.execute(
        """
        INSERT INTO entitlement_projection
            (user_id, provider_tier, provider_source, admin_override,
             effective_tier, revision, last_event_id, updated_at)
        SELECT id, 'free', 'none', NULL, 'free', 0, NULL, created_at FROM users
        """
    )
    op.execute(
        """
        INSERT INTO ad_config
            (id, enabled, source, enabled_slots_json, revision, actor_user_id, updated_at)
        VALUES (1, 0, 'house', '[]', 0, NULL, 0)
        """
    )
    op.execute(
        """
        INSERT INTO feature_flags
            (key, audience, config_json, revision, actor_user_id, updated_at)
        VALUES
            ('ad_system', 'off', '{}', 0, NULL, 0),
            ('mobile_ad_free', 'premium', '{}', 0, NULL, 0),
            ('podcast_subscriptions', 'off', '{}', 0, NULL, 0),
            ('topic_corpus', 'off', '{}', 0, NULL, 0),
            ('transcript_email', 'off', '{}', 0, NULL, 0)
        """
    )
    for table in ("entitlement_events", "audit_log"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_reject_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is append-only');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_reject_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is append-only');
            END
            """
        )


def downgrade() -> None:
    for table in ("entitlement_events", "audit_log"):
        op.execute(f"DROP TRIGGER {table}_reject_delete")
        op.execute(f"DROP TRIGGER {table}_reject_update")
    op.drop_index("ix_audit_target_created", table_name="audit_log")
    op.drop_index("ix_audit_actor_created", table_name="audit_log")
    op.drop_index("ix_audit_action_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("house_ads")
    op.drop_table("ad_config")
    op.drop_table("feature_flags")
    op.drop_table("entitlement_projection")
    op.drop_index("ix_entitlement_events_user_created", table_name="entitlement_events")
    op.drop_table("entitlement_events")
