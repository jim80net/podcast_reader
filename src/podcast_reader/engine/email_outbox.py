"""Memory-gated, content-free local outbox for explicit transcript email."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from podcast_reader.engine import library
from podcast_reader.engine.subscription_store import EmailIdempotencyConflictError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.engine.subscription_store import (
        EmailOutboxRecord,
        EpisodeRecord,
        SubscriptionStore,
    )

EMAIL_CONTENT_MAX_BYTES = 512 * 1024
EMAIL_CONTENT_MAX_LINES = 20_000
EMAIL_CLAIM_LEASE_SECONDS = 30
EMAIL_MAX_ATTEMPTS = 8
EMAIL_BACKOFF_BASE_SECONDS = 60
EMAIL_BACKOFF_MAX_SECONDS = 24 * 60 * 60
MAX_CAPABILITY_LIFETIME_SECONDS = 10 * 60
_MAX_TRANSCRIPT_ARTIFACT_BYTES = 8 * EMAIL_CONTENT_MAX_BYTES
_SUBJECT_RE = re.compile(r"usr_[A-Za-z0-9_-]{1,128}")
_SOURCE_ID_RE = re.compile(r"[0-9a-f]{64}")
_ACTION_ID_RE = re.compile(r"act_[A-Za-z0-9_-]{24}")
_DELIVERY_ID_RE = re.compile(r"del_[A-Za-z0-9_-]{24}")
_ERROR_CODES = frozenset(
    {
        "premium_feature_unavailable",
        "delivery_too_large",
        "idempotency_conflict",
        "delivery_unavailable",
        "email_not_verified",
        "artifact_unavailable",
    }
)


class Clock(Protocol):
    def __call__(self) -> datetime: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


class EmailFeatureUnavailableError(RuntimeError):
    """Stable error for local/free/stale email mutations and claims."""


class EmailOutboxError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EmailCapabilitySnapshot:
    schema_version: int
    subject: str
    entitlement_revision: int
    flags_revision: int
    transcript_email: bool
    expires_at: str


class EmailClaim(TypedDict):
    schema_version: int
    client_delivery_id: str
    claim_generation: int
    consent_kind: str
    title: str
    transcript_text: str
    content_sha256: str


class EmailOutboxManager:
    """Own the memory-only email capability and durable content-free outbox."""

    def __init__(
        self,
        store: SubscriptionStore,
        *,
        library_dir: Callable[[], Path],
        clock: Clock = _utc_now,
    ) -> None:
        self.store = store
        self._library_dir = library_dir
        self._clock = clock
        self._capability: EmailCapabilitySnapshot | None = None
        self._capability_lock = threading.Lock()

    def update_capability(self, snapshot: EmailCapabilitySnapshot) -> None:
        if snapshot.schema_version != 1:
            raise ValueError("unsupported email capability schema")
        if _SUBJECT_RE.fullmatch(snapshot.subject) is None:
            raise ValueError("invalid email capability subject")
        if snapshot.entitlement_revision < 0 or snapshot.flags_revision < 0:
            raise ValueError("invalid email capability revision")
        expires_at = _parse_time(snapshot.expires_at)
        now = self._clock()
        if expires_at <= now:
            raise ValueError("email capability is already expired")
        if expires_at > now + timedelta(seconds=MAX_CAPABILITY_LIFETIME_SECONDS):
            raise ValueError("email capability lifetime is too long")
        with self._capability_lock:
            self._capability = snapshot
        if snapshot.transcript_email:
            self._reconcile_completed(snapshot.subject)

    def clear_capability(self) -> None:
        with self._capability_lock:
            self._capability = None

    def _current_snapshot(self) -> EmailCapabilitySnapshot | None:
        with self._capability_lock:
            snapshot = self._capability
        if snapshot is None:
            return None
        try:
            return snapshot if _parse_time(snapshot.expires_at) > self._clock() else None
        except ValueError:
            return None

    def _available_snapshot(self) -> EmailCapabilitySnapshot | None:
        snapshot = self._current_snapshot()
        return snapshot if snapshot is not None and snapshot.transcript_email else None

    def is_available(self) -> bool:
        return self._available_snapshot() is not None

    def _require_available(self) -> EmailCapabilitySnapshot:
        snapshot = self._available_snapshot()
        if snapshot is None:
            raise EmailFeatureUnavailableError("premium_feature_unavailable")
        return snapshot

    def set_subscription_preference(
        self,
        subscription_id: str,
        *,
        subject: str,
        enabled: bool,
    ) -> dict[str, object]:
        if _SUBJECT_RE.fullmatch(subject) is None:
            raise ValueError("invalid email preference subject")
        if enabled and self._require_available().subject != subject:
            raise EmailFeatureUnavailableError("premium_feature_unavailable")
        preference = self.store.set_email_preference(
            subscription_id,
            subject,
            enabled=enabled,
            updated_at=_iso(self._clock()),
        )
        return {
            "subscription_id": subscription_id,
            "enabled": bool(preference is not None and preference["disabled_at"] is None),
            "consent_revision": preference["consent_revision"] if preference is not None else 0,
        }

    def preference_status(self, subscription_id: str, subject: str) -> dict[str, object]:
        if _SUBJECT_RE.fullmatch(subject) is None:
            raise ValueError("invalid email preference subject")
        preference = self.store.get_email_preference(subscription_id, subject)
        return {
            "subscription_id": subscription_id,
            "enabled": bool(preference is not None and preference["disabled_at"] is None),
            "consent_revision": preference["consent_revision"] if preference is not None else 0,
        }

    def record_subscription_completion(
        self, episode: EpisodeRecord, *, updated_at: str
    ) -> str | None:
        snapshot = self._available_snapshot()
        source_id = library.source_identity(episode["enclosure_url"])
        return self.store.mark_episode_terminal(
            episode["subscription_id"],
            episode["episode_key"],
            state="completed",
            updated_at=updated_at,
            email_subject=snapshot.subject if snapshot is not None else None,
            client_delivery_id=_new_id("eml") if snapshot is not None else None,
            source_id=source_id if snapshot is not None else None,
        )

    def _reconcile_completed(self, subject: str) -> None:
        """Recover consent-covered completions missed during a capability gap."""
        for episode in self.store.completed_email_candidates(subject):
            now = _iso(self._clock())
            self.store.insert_reconciled_email(
                subscription_id=episode["subscription_id"],
                episode_key=episode["episode_key"],
                client_delivery_id=_new_id("eml"),
                subject=subject,
                source_id=library.source_identity(episode["enclosure_url"]),
                created_at=now,
            )

    def create_manual(self, *, action_id: str, source_id: str) -> dict[str, object]:
        snapshot = self._require_available()
        if _ACTION_ID_RE.fullmatch(action_id) is None:
            raise ValueError("invalid manual email action")
        if _SOURCE_ID_RE.fullmatch(source_id) is None:
            raise ValueError("invalid transcript source")
        if library.get_entry(self._library_dir(), source_id) is None:
            raise KeyError(source_id)
        now = _iso(self._clock())
        try:
            item = self.store.insert_manual_email(
                client_delivery_id=_new_id("eml"),
                subject=snapshot.subject,
                source_id=source_id,
                action_id=action_id,
                created_at=now,
            )
        except EmailIdempotencyConflictError as exc:
            raise EmailOutboxError("idempotency_conflict") from exc
        return self._public_status(item)

    def list_status(self) -> list[dict[str, object]]:
        snapshot = self._current_snapshot()
        if snapshot is None:
            raise EmailFeatureUnavailableError("premium_feature_unavailable")
        return [
            self._public_status(item)
            for item in self.store.list_email_outbox(subject=snapshot.subject)
        ]

    @staticmethod
    def _public_status(item: EmailOutboxRecord) -> dict[str, object]:
        return {
            "client_delivery_id": item["client_delivery_id"],
            "subscription_id": item["subscription_id"],
            "consent_kind": item["consent_kind"],
            "state": item["state"],
            "attempts": item["attempts"],
            "error_code": item["error_code"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
            "delivered_at": item["delivered_at"],
        }

    def claim(self) -> EmailClaim | None:
        snapshot = self._require_available()
        now = self._clock()
        item = self.store.claim_email_outbox(
            snapshot.subject,
            claimed_at=_iso(now),
            claim_expires_at=_iso(now + timedelta(seconds=EMAIL_CLAIM_LEASE_SECONDS)),
        )
        if item is None:
            return None
        try:
            return self._materialize(item)
        except EmailOutboxError as exc:
            self.release(
                client_delivery_id=item["client_delivery_id"],
                claim_generation=item["claim_generation"],
                error_code=exc.code,
            )
            raise

    def _materialize(self, item: EmailOutboxRecord) -> EmailClaim:
        entry = library.get_entry(self._library_dir(), item["source_id"])
        if entry is None:
            raise EmailOutboxError("artifact_unavailable")
        title = unicodedata.normalize("NFC", entry["title"].strip())
        if not title or len(title) > 200 or _has_disallowed_control(title, allow_newlines=False):
            raise EmailOutboxError("delivery_too_large")
        transcript = _load_transcript(self._library_dir(), item["source_id"])
        return EmailClaim(
            schema_version=1,
            client_delivery_id=item["client_delivery_id"],
            claim_generation=item["claim_generation"],
            consent_kind=item["consent_kind"],
            title=title,
            transcript_text=transcript,
            content_sha256=hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        )

    def complete(
        self,
        *,
        client_delivery_id: str,
        claim_generation: int,
        delivery_id: str,
        delivered_at: str,
    ) -> dict[str, object]:
        if _DELIVERY_ID_RE.fullmatch(delivery_id) is None:
            raise ValueError("invalid server delivery identifier")
        _parse_time(delivered_at)
        item = self.store.complete_email_outbox(
            client_delivery_id,
            claim_generation=claim_generation,
            server_delivery_id=delivery_id,
            delivered_at=delivered_at,
            updated_at=_iso(self._clock()),
        )
        return self._public_status(item)

    def release(
        self,
        *,
        client_delivery_id: str,
        claim_generation: int,
        error_code: str,
    ) -> dict[str, object]:
        if error_code not in _ERROR_CODES:
            raise ValueError("invalid email delivery error code")
        items = {item["client_delivery_id"]: item for item in self.store.list_email_outbox()}
        item = items.get(client_delivery_id)
        if item is None:
            raise KeyError(client_delivery_id)
        exponent = max(0, int(item["attempts"]) - 1)
        delay = min(EMAIL_BACKOFF_BASE_SECONDS * (4**exponent), EMAIL_BACKOFF_MAX_SECONDS)
        now = self._clock()
        released = self.store.release_email_outbox(
            client_delivery_id,
            claim_generation=claim_generation,
            error_code=error_code,
            next_attempt_at=_iso(now + timedelta(seconds=delay)),
            updated_at=_iso(now),
        )
        return self._public_status(released)

    def cancel(self, client_delivery_id: str) -> dict[str, object]:
        item = self.store.cancel_email_outbox(client_delivery_id, updated_at=_iso(self._clock()))
        return self._public_status(item)


def _has_disallowed_control(value: str, *, allow_newlines: bool) -> bool:
    allowed = {"\t", "\n"} if allow_newlines else set()
    return any(
        unicodedata.category(character) == "Cc" and character not in allowed for character in value
    )


def _timestamp(value: float) -> str:
    if not math.isfinite(value):
        raise EmailOutboxError("artifact_unavailable")
    total = max(0, int(value))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _load_transcript(library_dir: Path, source_id: str) -> str:
    entry_dir = library.entry_dir(library_dir, source_id)
    segments: list[dict[str, Any]] | None = None
    for path in sorted(entry_dir.glob("*.json")):
        if path.stat().st_size > _MAX_TRANSCRIPT_ARTIFACT_BYTES:
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("segments"), list):
            segments = candidate["segments"]
            break
    if segments is None:
        raise EmailOutboxError("artifact_unavailable")
    if len(segments) > EMAIL_CONTENT_MAX_LINES:
        raise EmailOutboxError("delivery_too_large")
    lines: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise EmailOutboxError("delivery_unavailable")
        start = segment.get("start")
        text = segment.get("text")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or not math.isfinite(float(start))
            or not isinstance(text, str)
        ):
            raise EmailOutboxError("artifact_unavailable")
        cleaned = unicodedata.normalize("NFC", text.strip())
        if not cleaned:
            continue
        if _has_disallowed_control(cleaned, allow_newlines=False):
            raise EmailOutboxError("artifact_unavailable")
        lines.append(f"{_timestamp(float(start))} {cleaned}")
    transcript = "\n".join(lines) + "\n"
    if (
        not lines
        or len(lines) > EMAIL_CONTENT_MAX_LINES
        or len(transcript.encode("utf-8")) > EMAIL_CONTENT_MAX_BYTES
    ):
        raise EmailOutboxError("delivery_too_large")
    return transcript


def capability_fixture_path() -> str:
    return "contracts/v1/email/online-capability.json"
