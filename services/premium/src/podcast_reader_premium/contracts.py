from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Revision = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)]


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
