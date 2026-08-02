from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Revision = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)]
OpaqueToken = Annotated[str, Field(min_length=20, max_length=256)]
USER_CODE_PATTERN = (
    r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-"
    r"[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}$"
)


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
    model_config = ConfigDict(extra="forbid")

    source: Literal["none", "test_purchase", "admin"]
    revision: Revision


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ad_policy: Literal["none", "house", "paid"]
    podcast_subscriptions: bool
    transcript_email: bool
    mobile_ad_free: bool
    topic_corpus: bool


class EntitlementV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    subject: str
    tier: Literal["free", "premium"]
    entitlement: EntitlementSource
    capabilities: Capabilities
    flags_revision: Revision
    evaluated_at: datetime
    refresh_after: datetime

    @model_validator(mode="after")
    def validate_refresh_window(self) -> Self:
        if self.refresh_after <= self.evaluated_at:
            raise ValueError("refresh_after must be later than evaluated_at")
        return self


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
