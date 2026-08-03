from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    verification: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),)


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_browser_sessions_user_id", "user_id"),)


class DeviceAuthorization(Base):
    __tablename__ = "device_authorizations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    device_code_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_code_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    approving_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_polled_at: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    consumed_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class TokenFamily(Base):
    __tablename__ = "token_families"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(
        ForeignKey("token_families.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    used_at: Mapped[int | None] = mapped_column(Integer)
    replacement_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class AccessToken(Base):
    __tablename__ = "access_tokens"

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(
        ForeignKey("token_families.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_access_tokens_family_id", "family_id"),)


class StripeCustomer(Base):
    __tablename__ = "stripe_customers"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    customer_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)


class CheckoutAttempt(Base):
    __tablename__ = "checkout_attempts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    checkout_session_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'session_created', 'completed', 'expired', 'failed')",
            name="ck_checkout_attempts_status",
        ),
        Index("ix_checkout_attempts_user_created", "user_id", "created_at"),
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    provider_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    received_at: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[int | None] = mapped_column(Integer)
    retry_at: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_at: Mapped[int | None] = mapped_column(Integer)
    result_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'processing', 'processed', 'rejected', 'parked')",
            name="ck_payment_events_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_payment_events_attempts"),
        Index("ix_payment_events_state_retry", "state", "retry_at", "received_at"),
    )


class EntitlementEvent(Base):
    __tablename__ = "entitlement_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tier: Mapped[str | None] = mapped_column(String(16))
    source_reference: Mapped[str | None] = mapped_column(String(128))
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('provider_grant', 'provider_revoke', 'override_set', 'override_clear')",
            name="ck_entitlement_events_type",
        ),
        CheckConstraint(
            "tier IS NULL OR tier IN ('free', 'premium')", name="ck_entitlement_events_tier"
        ),
        CheckConstraint(
            "(event_type = 'provider_grant' AND tier IS NOT NULL AND tier = 'premium') OR "
            "(event_type = 'provider_revoke' AND (tier IS NULL OR tier = 'free')) OR "
            "(event_type = 'override_set' AND tier IS NOT NULL "
            "AND tier IN ('free', 'premium')) OR "
            "(event_type = 'override_clear' AND tier IS NULL)",
            name="ck_entitlement_events_type_tier",
        ),
        CheckConstraint("revision >= 1", name="ck_entitlement_events_revision"),
        UniqueConstraint("user_id", "revision", name="uq_entitlement_events_user_revision"),
        Index("ix_entitlement_events_user_created", "user_id", "created_at"),
        Index("uq_entitlement_events_source_reference", "source_reference", unique=True),
    )


class EntitlementProjection(Base):
    __tablename__ = "entitlement_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    provider_source: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    admin_override: Mapped[str | None] = mapped_column(String(16))
    effective_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("entitlement_events.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("provider_tier IN ('free', 'premium')", name="ck_projection_provider_tier"),
        CheckConstraint(
            "provider_source IN ('none', 'test_purchase')", name="ck_projection_provider_source"
        ),
        CheckConstraint(
            "admin_override IS NULL OR admin_override IN ('free', 'premium')",
            name="ck_projection_admin_override",
        ),
        CheckConstraint("effective_tier IN ('free', 'premium')", name="ck_projection_effective"),
        CheckConstraint("revision >= 0", name="ck_projection_revision"),
    )


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[str] = mapped_column(String(2048), nullable=False, default="{}")
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("audience IN ('off', 'all', 'free', 'premium')", name="ck_flags_audience"),
        CheckConstraint("revision >= 0", name="ck_flags_revision"),
    )


class AdConfig(Base):
    __tablename__ = "ad_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="house")
    enabled_slots_json: Mapped[str] = mapped_column(String(512), nullable=False, default="[]")
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_ad_config_singleton"),
        CheckConstraint("source = 'house'", name="ck_ad_config_house_only"),
        CheckConstraint("revision >= 0", name="ck_ad_config_revision"),
    )


class HouseAd(Base):
    __tablename__ = "house_ads"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    cta_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    starts_at: Mapped[int | None] = mapped_column(Integer)
    ends_at: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active')", name="ck_house_ads_status"),
        CheckConstraint("revision >= 1", name="ck_house_ads_revision"),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_house_ads_schedule",
        ),
    )


class EmailDeliveryReceipt(Base):
    __tablename__ = "email_delivery_receipts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_delivery_id: Mapped[str] = mapped_column(String(40), nullable=False)
    consent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    sink: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False)
    delivered_at: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "consent_kind IN ('subscription_completion', 'manual')",
            name="ck_email_receipts_consent",
        ),
        CheckConstraint("sink = 'dev_maildir'", name="ck_email_receipts_sink"),
        CheckConstraint(
            "state IN ('processing', 'delivered', 'failed')", name="ck_email_receipts_state"
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code = 'delivery_unavailable'",
            name="ck_email_receipts_error",
        ),
        CheckConstraint(
            "(state = 'delivered' AND delivered_at IS NOT NULL AND error_code IS NULL) OR "
            "(state = 'processing' AND delivered_at IS NULL AND error_code IS NULL) OR "
            "(state = 'failed' AND delivered_at IS NULL "
            "AND error_code = 'delivery_unavailable')",
            name="ck_email_receipts_state_fields",
        ),
        CheckConstraint("attempts >= 1", name="ck_email_receipts_attempts"),
        UniqueConstraint("user_id", "client_delivery_id", name="uq_email_receipts_user_client"),
        Index("ix_email_receipts_state_updated", "state", "updated_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    after_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    delta_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    request_id: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_audit_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_target_created", "target_kind", "target_id", "created_at"),
        Index("ix_audit_action_created", "action", "created_at"),
    )
