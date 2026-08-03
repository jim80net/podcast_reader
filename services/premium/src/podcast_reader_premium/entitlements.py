"""Entitlement ledger, projections, and explicit persisted repair support.

Normal reads only assert projection integrity. The ``repair-entitlements`` CLI is
the sole recovery path that persists a projection rebuilt from the append-only ledger.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contracts import Capabilities, EntitlementSource, EntitlementV1
from .models import AdConfig, AuditLog, EntitlementEvent, EntitlementProjection, FeatureFlag, User
from .security import now_epoch, record_id

Tier = Literal["free", "premium"]
EventType = Literal["provider_grant", "provider_revoke", "override_set", "override_clear"]

FLAG_DEFAULTS: dict[str, str] = {
    "ad_system": "off",
    "mobile_ad_free": "premium",
    "podcast_subscriptions": "off",
    "topic_corpus": "off",
    "transcript_email": "off",
}
FLAG_AUDIENCES = frozenset({"off", "all", "free", "premium"})
AD_SYSTEM_AUDIENCES = frozenset({"off", "free"})
AD_SLOTS = frozenset({"library", "reader", "mobile_home"})


def require_entitlement_configuration(database: Session) -> None:
    flags = {item.key: item for item in database.scalars(select(FeatureFlag)).all()}
    if set(flags) != set(FLAG_DEFAULTS):
        raise RuntimeError("feature flag registry does not match the code-owned keys")
    for flag in flags.values():
        try:
            config = json.loads(flag.config_json)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("feature flag config is invalid") from exc
        valid_audiences = AD_SYSTEM_AUDIENCES if flag.key == "ad_system" else FLAG_AUDIENCES
        if flag.audience not in valid_audiences or config != {}:
            raise RuntimeError("feature flag state is invalid")
    ad_config = database.get(AdConfig, 1)
    if ad_config is None or ad_config.source != "house":
        raise RuntimeError("house ad configuration is missing")
    try:
        slots = json.loads(ad_config.enabled_slots_json)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("house ad slot configuration is invalid") from exc
    if not isinstance(slots, list) or any(not isinstance(item, str) for item in slots):
        raise RuntimeError("house ad slot configuration is invalid")
    if len(slots) != len(set(slots)) or set(slots) - AD_SLOTS:
        raise RuntimeError("house ad slot configuration is invalid")
    missing_projection = database.scalar(
        select(func.count())
        .select_from(User)
        .outerjoin(EntitlementProjection, EntitlementProjection.user_id == User.id)
        .where(EntitlementProjection.user_id.is_(None))
    )
    if missing_projection:
        raise RuntimeError("one or more users are missing entitlement projections")


def ensure_projection(
    database: Session, user_id: str, *, timestamp: int | None = None
) -> EntitlementProjection:
    projection = database.get(EntitlementProjection, user_id)
    if projection is not None:
        return projection
    projection = EntitlementProjection(
        user_id=user_id,
        provider_tier="free",
        provider_source="none",
        admin_override=None,
        effective_tier="free",
        revision=0,
        last_event_id=None,
        updated_at=timestamp if timestamp is not None else now_epoch(),
    )
    database.add(projection)
    database.flush()
    return projection


def apply_entitlement_event(
    database: Session,
    *,
    user_id: str,
    event_type: EventType,
    tier: Tier | None,
    actor_user_id: str | None,
    reason: str,
    source_reference: str | None = None,
    timestamp: int | None = None,
) -> EntitlementProjection:
    when = timestamp if timestamp is not None else now_epoch()
    projection = ensure_projection(database, user_id, timestamp=when)
    next_revision = projection.revision + 1
    event = EntitlementEvent(
        id=record_id("ent"),
        user_id=user_id,
        event_type=event_type,
        tier=tier,
        source_reference=source_reference,
        actor_user_id=actor_user_id,
        reason=reason,
        revision=next_revision,
        created_at=when,
    )
    if event_type == "provider_grant":
        if tier != "premium" or not source_reference:
            raise ValueError("provider grant requires premium tier and a source reference")
        projection.provider_tier = "premium"
        projection.provider_source = "test_purchase"
    elif event_type == "provider_revoke":
        if tier not in {None, "free"}:
            raise ValueError("provider revoke may only restore free")
        projection.provider_tier = "free"
        projection.provider_source = "none"
    elif event_type == "override_set":
        if tier not in {"free", "premium"}:
            raise ValueError("override requires a tier")
        projection.admin_override = tier
    elif event_type == "override_clear":
        if tier is not None:
            raise ValueError("override clear cannot carry a tier")
        projection.admin_override = None
    projection.effective_tier = projection.admin_override or projection.provider_tier
    projection.revision = next_revision
    projection.last_event_id = event.id
    projection.updated_at = when
    database.add(event)
    database.flush()
    return projection


def rebuild_projection(database: Session, user_id: str) -> dict[str, object]:
    provider_tier = "free"
    provider_source = "none"
    override: str | None = None
    revision = 0
    last_event_id: str | None = None
    events = database.scalars(
        select(EntitlementEvent)
        .where(EntitlementEvent.user_id == user_id)
        .order_by(EntitlementEvent.revision)
    )
    for event in events:
        if event.revision != revision + 1:
            raise ValueError("entitlement ledger revision gap")
        if event.event_type == "provider_grant":
            provider_tier = "premium"
            provider_source = "test_purchase"
        elif event.event_type == "provider_revoke":
            provider_tier = "free"
            provider_source = "none"
        elif event.event_type == "override_set":
            override = event.tier
        elif event.event_type == "override_clear":
            override = None
        revision = event.revision
        last_event_id = event.id
    return {
        "provider_tier": provider_tier,
        "provider_source": provider_source,
        "admin_override": override,
        "effective_tier": override or provider_tier,
        "revision": revision,
        "last_event_id": last_event_id,
    }


def _verified_projection(database: Session, user_id: str) -> EntitlementProjection:
    """Load a projection only after verifying it against the authoritative ledger."""
    projection = database.get(EntitlementProjection, user_id)
    if projection is None:
        raise ValueError("entitlement projection is missing")
    rebuilt = rebuild_projection(database, user_id)
    for key, value in rebuilt.items():
        if getattr(projection, key) != value:
            raise ValueError(f"entitlement projection mismatch for {key}")
    return projection


def repair_projection(database: Session, user_id: str, *, timestamp: int) -> bool:
    """Persist a ledger-derived projection; return whether stored state changed."""
    rebuilt = rebuild_projection(database, user_id)
    projection = database.get(EntitlementProjection, user_id)
    changed = projection is None or any(
        getattr(projection, key) != value for key, value in rebuilt.items()
    )
    if projection is None:
        projection = EntitlementProjection(user_id=user_id, updated_at=timestamp, **rebuilt)
        database.add(projection)
    elif changed:
        for key, value in rebuilt.items():
            setattr(projection, key, value)
        projection.updated_at = timestamp
    database.flush()
    return changed


def next_config_revision(database: Session) -> int:
    flag_revision = database.scalar(select(func.max(FeatureFlag.revision))) or 0
    ad_revision = database.scalar(select(func.max(AdConfig.revision))) or 0
    return max(flag_revision, ad_revision) + 1


def _flag_enabled(flag: FeatureFlag | None, tier: Tier) -> bool:
    audience = flag.audience if flag is not None else "off"
    return audience == "all" or audience == tier


def evaluate_entitlements(
    database: Session, user_id: str, *, at: datetime | None = None
) -> EntitlementV1:
    evaluated = (at or datetime.now(UTC)).replace(microsecond=0)
    # Ratified #119 semantics: the ledger is authoritative and every protected read fails closed.
    projection = _verified_projection(database, user_id)
    tier: Tier = "premium" if projection.effective_tier == "premium" else "free"
    flag_rows = {item.key: item for item in database.scalars(select(FeatureFlag)).all()}
    unknown = set(flag_rows) - set(FLAG_DEFAULTS)
    if unknown:
        raise ValueError("database contains an unknown feature flag")
    ad_config = database.get(AdConfig, 1)
    flags_revision = max(
        [item.revision for item in flag_rows.values()]
        + ([ad_config.revision] if ad_config is not None else [0])
    )
    premium = tier == "premium"
    ad_policy: Literal["none", "house"] = "none"
    if (
        not premium
        and ad_config is not None
        and ad_config.enabled
        and _flag_enabled(flag_rows.get("ad_system"), tier)
    ):
        ad_policy = "house"
    source: Literal["none", "test_purchase", "admin"]
    if projection.admin_override is not None:
        source = "admin"
    elif projection.provider_source == "test_purchase":
        source = "test_purchase"
    else:
        source = "none"
    return EntitlementV1(
        subject=user_id,
        tier=tier,
        entitlement=EntitlementSource(source=source, revision=projection.revision),
        capabilities=Capabilities(
            ad_policy=ad_policy,
            podcast_subscriptions=premium
            and _flag_enabled(flag_rows.get("podcast_subscriptions"), tier),
            transcript_email=premium and _flag_enabled(flag_rows.get("transcript_email"), tier),
            mobile_ad_free=premium and _flag_enabled(flag_rows.get("mobile_ad_free"), tier),
            topic_corpus=premium and _flag_enabled(flag_rows.get("topic_corpus"), tier),
        ),
        flags_revision=flags_revision,
        evaluated_at=evaluated,
        refresh_after=evaluated + timedelta(minutes=5),
    )


def entitlement_etag(value: EntitlementV1) -> str:
    identity = f"v1:{value.entitlement.revision}:{value.flags_revision}:{value.tier}"
    return '"' + hashlib.sha256(identity.encode()).hexdigest() + '"'


def canonical_state(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def record_audit(
    database: Session,
    *,
    actor_user_id: str | None,
    action: str,
    target_kind: str,
    target_id: str,
    before: Mapping[str, object],
    after: Mapping[str, object],
    reason: str,
    request_id: str,
    timestamp: int | None = None,
) -> AuditLog:
    before_json = canonical_state(before)
    after_json = canonical_state(after)
    delta = canonical_state({"before": before, "after": after})
    if len(delta) > 4096:
        raise ValueError("audit delta is too large")
    audit = AuditLog(
        id=record_id("aud"),
        actor_user_id=actor_user_id,
        action=action,
        target_kind=target_kind,
        target_id=target_id,
        before_digest=hashlib.sha256(before_json.encode()).hexdigest(),
        after_digest=hashlib.sha256(after_json.encode()).hexdigest(),
        delta_json=delta,
        reason=reason,
        request_id=request_id,
        created_at=timestamp if timestamp is not None else now_epoch(),
    )
    database.add(audit)
    return audit
