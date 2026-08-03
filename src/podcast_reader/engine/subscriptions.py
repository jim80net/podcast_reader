"""Premium-gated, engine-local podcast subscription polling."""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from podcast_reader.engine.subscription_feed import (
    CachingResolver,
    FeedError,
    FeedFetcher,
    FeedResponse,
    FeedTemporaryError,
    SafeFeedFetcher,
    parse_feed,
    validate_feed_url,
)
from podcast_reader.engine.subscription_store import (
    EpisodeRecord,
    SubscriptionRecord,
    SubscriptionStore,
    episode_record,
)

POLL_INTERVAL_SECONDS = 30 * 60
START_DELAY_SECONDS = 15
MAX_SCHEDULED_PER_TICK = 3
ERROR_BACKOFF_SECONDS = 30 * 60
MAX_CAPABILITY_LIFETIME_SECONDS = 10 * 60
_SUBJECT_RE = re.compile(r"usr_[A-Za-z0-9_-]{1,128}")


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


class PremiumFeatureUnavailableError(RuntimeError):
    """Stable API error for local/free/stale/unknown subscription mutations."""


@dataclass(frozen=True)
class OnlineCapabilitySnapshot:
    schema_version: int
    subject: str
    entitlement_revision: int
    flags_revision: int
    podcast_subscriptions: bool
    expires_at: str


@dataclass(frozen=True)
class PollResult:
    subscription: SubscriptionRecord
    discovered_count: int
    not_modified: bool


class SubscriptionManager:
    """Own the memory-only capability and the bounded polling scheduler."""

    def __init__(
        self,
        store: SubscriptionStore,
        *,
        fetcher: FeedFetcher | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self.store = store
        self._fetcher = fetcher or SafeFeedFetcher()
        self._clock = clock
        self._capability: OnlineCapabilitySnapshot | None = None
        self._capability_lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._in_flight_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._scheduler_stop = threading.Event()
        self._thread_lock = threading.Lock()
        self._armed = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Arm scheduling; Local remains thread-free until a fresh capability arrives."""
        self._armed = True
        if not self.is_available():
            return
        self._ensure_scheduler()

    def _ensure_scheduler(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                if not self._scheduler_stop.is_set():
                    return
                self._thread.join(timeout=0.2)
                if self._thread.is_alive():
                    # A bounded in-flight request is unwinding. Reuse that
                    # thread for the newly fresh generation without overlap.
                    self._scheduler_stop.clear()
                    return
            self._scheduler_stop.clear()
            self._thread = threading.Thread(
                target=self._run_scheduler,
                name="subscription-poller",
                daemon=True,
            )
            self._thread.start()

    def shutdown(self) -> None:
        self._stopping.set()
        self._scheduler_stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=12)
        self.store.close()

    def update_capability(self, snapshot: OnlineCapabilitySnapshot) -> None:
        if snapshot.schema_version != 1:
            raise ValueError("unsupported online capability schema")
        if _SUBJECT_RE.fullmatch(snapshot.subject) is None:
            raise ValueError("invalid capability subject")
        if snapshot.entitlement_revision < 0 or snapshot.flags_revision < 0:
            raise ValueError("invalid capability revision")
        expires_at = _parse_time(snapshot.expires_at)
        now = self._clock()
        if expires_at <= now:
            raise ValueError("online capability is already expired")
        if expires_at > now + timedelta(seconds=MAX_CAPABILITY_LIFETIME_SECONDS):
            raise ValueError("online capability lifetime is too long")
        with self._capability_lock:
            previous = self._capability
            was_available = bool(
                previous is not None
                and previous.podcast_subscriptions
                and _parse_time(previous.expires_at) > now
            )
            self._capability = snapshot
        account_changed = previous is not None and previous.subject != snapshot.subject
        if snapshot.podcast_subscriptions and (not was_available or account_changed):
            self.store.accelerate_checks(
                _iso(self._clock() + timedelta(seconds=START_DELAY_SECONDS))
            )
        if snapshot.podcast_subscriptions and self._armed:
            self._ensure_scheduler()
        elif not snapshot.podcast_subscriptions:
            self._scheduler_stop.set()
        self._wake.set()

    def clear_capability(self) -> None:
        with self._capability_lock:
            self._capability = None
        self._scheduler_stop.set()
        self._wake.set()

    def is_available(self) -> bool:
        with self._capability_lock:
            snapshot = self._capability
        if snapshot is None or not snapshot.podcast_subscriptions:
            return False
        try:
            return _parse_time(snapshot.expires_at) > self._clock()
        except ValueError:
            return False

    def _require_available(self) -> None:
        if not self.is_available():
            raise PremiumFeatureUnavailableError("premium_feature_unavailable")

    def list_subscriptions(self) -> list[SubscriptionRecord]:
        return self.store.list_subscriptions()

    def create_subscription(self, feed_url: str) -> SubscriptionRecord:
        self._require_available()
        canonical, _origin = validate_feed_url(feed_url)
        response = self._fetcher.fetch(canonical, should_continue=self.is_available)
        if response.status == 304:
            raise FeedError("initial feed request returned not modified")
        final_url, origin = validate_feed_url(response.final_url)
        parsed = parse_feed(
            response.content,
            feed_url=final_url,
            resolver=CachingResolver(),
        )
        self._require_available()
        now = self._clock()
        subscription_id = f"sub_{uuid.uuid4().hex}"
        now_text = _iso(now)
        episodes = [
            episode_record(
                subscription_id=subscription_id,
                episode_key=episode.episode_key,
                guid=episode.guid,
                enclosure_url=episode.enclosure_url,
                title=episode.title,
                published_at=episode.published_at,
                now=now_text,
            )
            for episode in parsed.episodes
        ]
        return self.store.insert_subscription(
            subscription_id=subscription_id,
            feed_url=final_url,
            title=parsed.title,
            normalized_origin=origin,
            etag=response.etag,
            last_modified=response.last_modified,
            checked_at=now_text,
            next_check_at=_iso(now + timedelta(seconds=self._jitter(subscription_id))),
            baseline_episodes=episodes,
        )

    def delete_subscription(self, subscription_id: str) -> None:
        self._require_available()
        self.store.delete_subscription(subscription_id)

    def poll_subscription(self, subscription_id: str) -> PollResult:
        self._require_available()
        with self._claim(subscription_id):
            subscription = self.store.get_subscription(subscription_id)
            try:
                response = self._fetcher.fetch(
                    subscription["feed_url"],
                    etag=subscription["etag"],
                    last_modified=subscription["last_modified"],
                    should_continue=self.is_available,
                )
                self._require_available()
                return self._record_response(subscription, response)
            except PremiumFeatureUnavailableError:
                raise
            except FeedError as exc:
                self._require_available()
                now = self._clock()
                delay = (
                    exc.retry_after_seconds
                    if isinstance(exc, FeedTemporaryError)
                    else ERROR_BACKOFF_SECONDS
                )
                self.store.record_error(
                    subscription_id,
                    checked_at=_iso(now),
                    next_check_at=_iso(now + timedelta(seconds=delay)),
                    detail=str(exc),
                )
                raise

    def _record_response(
        self, subscription: SubscriptionRecord, response: FeedResponse
    ) -> PollResult:
        now = self._clock()
        next_check = _iso(now + timedelta(seconds=self._jitter(subscription["id"])))
        if response.status == 304:
            updated = self.store.record_not_modified(
                subscription["id"], checked_at=_iso(now), next_check_at=next_check
            )
            return PollResult(subscription=updated, discovered_count=0, not_modified=True)
        parsed = parse_feed(
            response.content,
            feed_url=response.final_url,
            resolver=CachingResolver(),
        )
        episodes: list[EpisodeRecord] = [
            episode_record(
                subscription_id=subscription["id"],
                episode_key=episode.episode_key,
                guid=episode.guid,
                enclosure_url=episode.enclosure_url,
                title=episode.title,
                published_at=episode.published_at,
                now=_iso(now),
            )
            for episode in parsed.episodes
        ]
        updated, discovered = self.store.record_poll(
            subscription["id"],
            title=parsed.title,
            etag=response.etag,
            last_modified=response.last_modified,
            checked_at=_iso(now),
            next_check_at=next_check,
            episodes=episodes,
        )
        return PollResult(
            subscription=updated,
            discovered_count=discovered,
            not_modified=False,
        )

    class _Claim:
        def __init__(self, manager: SubscriptionManager, subscription_id: str) -> None:
            self._manager = manager
            self._subscription_id = subscription_id

        def __enter__(self) -> None:
            with self._manager._in_flight_lock:
                if self._subscription_id in self._manager._in_flight:
                    raise FeedError("subscription poll is already in progress")
                self._manager._in_flight.add(self._subscription_id)

        def __exit__(self, *_args: object) -> None:
            with self._manager._in_flight_lock:
                self._manager._in_flight.discard(self._subscription_id)

    def _claim(self, subscription_id: str) -> _Claim:
        return self._Claim(self, subscription_id)

    @staticmethod
    def _jitter(subscription_id: str) -> int:
        digest = hashlib.sha256(subscription_id.encode()).digest()
        offset = int.from_bytes(digest[:2], "big") % 601 - 300
        return POLL_INTERVAL_SECONDS + offset

    def _run_scheduler(self) -> None:
        while not self._stopping.is_set() and not self._scheduler_stop.is_set():
            if not self.is_available():
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            due = self.store.due_subscription_ids(_iso(self._clock()), limit=MAX_SCHEDULED_PER_TICK)
            if not due:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            for subscription_id in due:
                if self._stopping.is_set() or not self.is_available():
                    break
                try:
                    self.poll_subscription(subscription_id)
                except (FeedError, KeyError, PremiumFeatureUnavailableError):
                    continue


def capability_fixture_path() -> str:
    """Stable package-relative path used by both engine and desktop contract tests."""
    return "contracts/v1/subscriptions/online-capability.json"
