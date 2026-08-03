"""House-only ad inventory selection for fresh online-free accounts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .contracts import AdInventoryItemV1, AdInventoryV1, AdSlot
from .entitlements import evaluate_entitlements
from .models import AdConfig, HouseAd

MAX_INVENTORY_ITEMS = 10
INVENTORY_TTL = timedelta(minutes=5)


def inventory_for_slot(
    database: Session,
    user_id: str,
    slot: AdSlot,
    *,
    at: datetime | None = None,
) -> AdInventoryV1 | None:
    """Return one snapshot-consistent inventory, or no content when ineligible."""
    evaluated_at = (at or datetime.now(UTC)).replace(microsecond=0)
    entitlement = evaluate_entitlements(database, user_id, at=evaluated_at)
    if entitlement.tier != "free" or entitlement.capabilities.ad_policy != "house":
        return None

    config = database.get(AdConfig, 1)
    if config is None or not config.enabled or config.source != "house":
        return None
    try:
        enabled_slots = json.loads(config.enabled_slots_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(enabled_slots, list) or slot not in enabled_slots:
        return None

    timestamp = int(evaluated_at.timestamp())
    ads = database.scalars(
        select(HouseAd)
        .where(
            HouseAd.status == "active",
            or_(HouseAd.starts_at.is_(None), HouseAd.starts_at <= timestamp),
            or_(HouseAd.ends_at.is_(None), HouseAd.ends_at > timestamp),
        )
        .order_by(func.coalesce(HouseAd.starts_at, 0), HouseAd.id)
        .limit(MAX_INVENTORY_ITEMS)
    ).all()
    if not ads:
        return None

    items = [
        AdInventoryItemV1(
            id=ad.id,
            revision=ad.revision,
            kind="text",
            title=ad.title,
            body=ad.body,
            cta_url=ad.cta_url,
        )
        for ad in ads
    ]
    revision_input = json.dumps(
        {
            "config": config.revision,
            "slot": slot,
            "items": [[ad.id, ad.revision] for ad in ads],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    inventory_revision = int.from_bytes(hashlib.sha256(revision_input).digest()[:8], "big") >> 1

    expiry = min(entitlement.refresh_after, evaluated_at + INVENTORY_TTL)
    schedule_ends = [
        datetime.fromtimestamp(ad.ends_at, UTC)
        for ad in ads
        if ad.ends_at is not None and ad.ends_at > timestamp
    ]
    if schedule_ends:
        expiry = min(expiry, *schedule_ends)

    # Defense in depth at the serialization boundary: premium is never inventory-eligible.
    if entitlement.tier != "free":
        return None
    return AdInventoryV1(
        slot=slot,
        inventory_revision=inventory_revision,
        expires_at=expiry,
        items=items,
    )
