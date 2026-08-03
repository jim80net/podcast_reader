from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

MAX_SAFE_REVISION = 9_007_199_254_740_991
Revision = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)]
EntitlementRevision = Annotated[int, Field(ge=0, le=MAX_SAFE_REVISION)]
OpaqueToken = Annotated[str, Field(min_length=20, max_length=256)]
EMAIL_CONTENT_MAX_BYTES = 512 * 1024
EMAIL_CONTENT_MAX_LINES = 20_000
EMAIL_REQUEST_MAX_BYTES = EMAIL_CONTENT_MAX_BYTES * 6 + 4096
USER_CODE_PATTERN = (
    r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-"
    r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}$"
)
CANONICAL_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _canonical_utc(value: object) -> datetime:
    if isinstance(value, str):
        if CANONICAL_UTC_PATTERN.fullmatch(value) is None:
            raise ValueError("timestamp must be canonical UTC seconds")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        if value.utcoffset() != timedelta(0) or value.microsecond != 0:
            raise ValueError("timestamp must be canonical UTC seconds")
        return value.astimezone(UTC)
    raise ValueError("timestamp must be canonical UTC seconds")


CanonicalUtc = Annotated[datetime, BeforeValidator(_canonical_utc)]


class CurrentUserV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Opaque to consumers: only non-blank and bounded by the backing column.
    id: Annotated[str, Field(pattern=r"^\S{1,40}$", min_length=1, max_length=40)]


class DeviceAuthorizationStartV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    device_code: OpaqueToken
    user_code: Annotated[str, Field(pattern=USER_CODE_PATTERN)]
    verification_uri: Annotated[str, Field(pattern=r"^https://", max_length=2048)]
    expires_in: Annotated[int, Field(gt=0)]
    interval: Annotated[int, Field(gt=0)]


class TokenResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    access_token: OpaqueToken
    token_type: Literal["Bearer"]
    expires_in: Annotated[int, Field(gt=0)]
    refresh_token: OpaqueToken


class TokenRevokeRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    refresh_token: OpaqueToken


class NativeAuthErrorV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal[
        "authorization_pending",
        "slow_down",
        "expired_token",
        "access_denied",
        "refresh_token_reused",
    ]
    message: Annotated[str, Field(min_length=1, max_length=200)]
    request_id: Annotated[str, Field(min_length=1, max_length=64)]


class EntitlementSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["none", "test_purchase", "admin"]
    revision: EntitlementRevision


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ad_policy: Literal["none", "house"]
    podcast_subscriptions: bool
    transcript_email: bool
    mobile_ad_free: bool
    topic_corpus: bool


class EntitlementV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    subject: str
    tier: Literal["free", "premium"]
    entitlement: EntitlementSource
    capabilities: Capabilities
    flags_revision: EntitlementRevision
    evaluated_at: CanonicalUtc
    refresh_after: CanonicalUtc

    @model_validator(mode="after")
    def validate_refresh_window(self) -> Self:
        if self.refresh_after <= self.evaluated_at:
            raise ValueError("refresh_after must be later than evaluated_at")
        if self.tier == "free":
            if self.entitlement.source not in {"none", "admin"}:
                raise ValueError("free entitlement source is invalid")
            if self.capabilities.ad_policy not in {"none", "house"} or any(
                (
                    self.capabilities.podcast_subscriptions,
                    self.capabilities.transcript_email,
                    self.capabilities.mobile_ad_free,
                    self.capabilities.topic_corpus,
                )
            ):
                raise ValueError("free entitlement capabilities are invalid")
        elif self.entitlement.source not in {"test_purchase", "admin"}:
            raise ValueError("premium entitlement source is invalid")
        elif self.capabilities.ad_policy != "none":
            raise ValueError("premium ad policy is invalid")
        return self


AdSlot = Literal["library", "reader", "mobile_home"]


class AdInventoryItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: Annotated[str, Field(pattern=r"^ad_[A-Za-z0-9_-]+$", min_length=4, max_length=40)]
    revision: Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807)]
    kind: Literal["text"]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    body: Annotated[str, Field(min_length=1, max_length=500)]
    cta_url: Annotated[str, Field(pattern=r"^https://", max_length=2048)]

    @field_validator("cta_url")
    @classmethod
    def validate_cta_url(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("cta_url must not contain whitespace")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("cta_url must be a valid HTTPS URL") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("cta_url must be HTTPS without credentials")
        return value


class AdInventoryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    slot: AdSlot
    inventory_revision: Revision
    expires_at: datetime
    items: Annotated[list[AdInventoryItemV1], Field(min_length=1, max_length=10)]


def _has_disallowed_control(value: str, *, allow_newlines: bool) -> bool:
    allowed = {"\t", "\n"} if allow_newlines else set()
    return any(
        unicodedata.category(character) == "Cc" and character not in allowed for character in value
    )


class EmailDeliveryRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    client_delivery_id: Annotated[
        str, Field(pattern=r"^eml_[A-Za-z0-9_-]{24}$", min_length=28, max_length=28)
    ]
    consent_kind: Literal["subscription_completion", "manual"]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    transcript_text: Annotated[str, Field(min_length=1)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if value != unicodedata.normalize("NFC", value) or _has_disallowed_control(
            value, allow_newlines=False
        ):
            raise ValueError("title must be NFC text without control characters")
        return value

    @field_validator("transcript_text")
    @classmethod
    def validate_transcript(cls, value: str) -> str:
        encoded = value.encode("utf-8")
        if (
            len(encoded) > EMAIL_CONTENT_MAX_BYTES
            or value.count("\n") + 1 > EMAIL_CONTENT_MAX_LINES
        ):
            raise ValueError("transcript exceeds the delivery bound")
        if value != unicodedata.normalize("NFC", value) or _has_disallowed_control(
            value, allow_newlines=True
        ):
            raise ValueError("transcript must be NFC plain text")
        return value

    @model_validator(mode="after")
    def validate_content_digest(self) -> Self:
        if hashlib.sha256(self.transcript_text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match transcript_text")
        return self


class EmailDeliveryV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    delivery_id: Annotated[
        str, Field(pattern=r"^del_[A-Za-z0-9_-]{24}$", min_length=28, max_length=28)
    ]
    client_delivery_id: Annotated[
        str, Field(pattern=r"^eml_[A-Za-z0-9_-]{24}$", min_length=28, max_length=28)
    ]
    state: Literal["delivered"]
    destination: Literal["dev_maildir"]
    delivered_at: datetime


class EmailDeliveryErrorV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal[
        "premium_feature_unavailable",
        "delivery_too_large",
        "idempotency_conflict",
        "delivery_unavailable",
        "email_not_verified",
    ]
    message: Annotated[str, Field(min_length=1, max_length=200)]
    request_id: Annotated[str, Field(min_length=1, max_length=64)]


def default_free_entitlement(subject: str, at: datetime | None = None) -> EntitlementV1:
    evaluated = (at or datetime.now(UTC)).replace(microsecond=0)
    return EntitlementV1(
        subject=subject,
        tier="free",
        entitlement=EntitlementSource(source="none", revision=0),
        capabilities=Capabilities(
            ad_policy="none",
            podcast_subscriptions=False,
            transcript_email=False,
            mobile_ad_free=False,
            topic_corpus=False,
        ),
        flags_revision=0,
        evaluated_at=evaluated,
        refresh_after=evaluated + timedelta(minutes=5),
    )
