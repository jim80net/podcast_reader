"""Premium-gated, engine-local podcast subscription polling."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Protocol

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

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.engine.email_outbox import EmailOutboxManager
    from podcast_reader.engine.jobs import JobStore

POLL_INTERVAL_SECONDS = 30 * 60
START_DELAY_SECONDS = 15
MAX_SCHEDULED_PER_TICK = 3
MAX_JOBS_PER_POLL = 3
ERROR_BACKOFF_SECONDS = 30 * 60
MAX_CAPABILITY_LIFETIME_SECONDS = 10 * 60
RECONCILIATION_INTERVAL_SECONDS = 30
_SUBJECT_RE = re.compile(r"usr_[A-Za-z0-9_-]{1,128}")
logger = logging.getLogger(__name__)


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
        job_store: JobStore | None = None,
        library_has_source: Callable[[str], bool] | None = None,
        email_outbox: EmailOutboxManager | None = None,
    ) -> None:
        self.store = store
        self._fetcher = fetcher or SafeFeedFetcher()
        self._clock = clock
        self._job_store = job_store
        self._library_has_source = library_has_source or (lambda _source: False)
        self._email_outbox = email_outbox
        self._capability: OnlineCapabilitySnapshot | None = None
        self._capability_lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._in_flight_condition = threading.Condition()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._scheduler_stop = threading.Event()
        self._thread_lock = threading.Lock()
        self._armed = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Arm scheduling; Local remains thread-free until a fresh capability arrives."""
        self._reconcile_jobs()
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
        with self._in_flight_condition:
            self._stopping.set()
        self._scheduler_stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=12)
        with self._in_flight_condition:
            self._in_flight_condition.wait_for(lambda: not self._in_flight)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join()
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
        if self._stopping.is_set():
            return False
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
            handed_off = self._handoff_discovered(
                subscription_id,
                limit=MAX_JOBS_PER_POLL,
            )
            try:
                response = self._fetcher.fetch(
                    subscription["feed_url"],
                    etag=subscription["etag"],
                    last_modified=subscription["last_modified"],
                    should_continue=self.is_available,
                )
                self._require_available()
                return self._record_response(
                    subscription,
                    response,
                    handoff_limit=MAX_JOBS_PER_POLL - handed_off,
                )
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
        self,
        subscription: SubscriptionRecord,
        response: FeedResponse,
        *,
        handoff_limit: int,
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
        self._handoff_discovered(subscription["id"], limit=handoff_limit)
        return PollResult(
            subscription=updated,
            discovered_count=discovered,
            not_modified=False,
        )

    @staticmethod
    def _idempotency_key(episode: EpisodeRecord) -> str:
        return f"subscription:{episode['subscription_id']}:{episode['episode_key']}"

    def _handoff_discovered(self, subscription_id: str, *, limit: int) -> int:
        if self._job_store is None or limit <= 0:
            return 0
        episodes = self.store.discovered_episodes(
            subscription_id,
            limit=limit,
        )
        for episode in episodes:
            self._require_available()
            job = self._job_store.submit(
                episode["enclosure_url"],
                episode["title"],
                idempotency_key=self._idempotency_key(episode),
            )
            self.store.mark_episode_queued(
                episode["subscription_id"],
                episode["episode_key"],
                job_id=job["id"],
                updated_at=_iso(self._clock()),
            )
        return len(episodes)

    def _reconcile_jobs(self) -> None:
        if self._job_store is None:
            return
        for episode in self.store.episodes_for_reconciliation():
            job = None
            if episode["state"] == "discovered":
                job = self._job_store.get_by_idempotency_key(self._idempotency_key(episode))
                if job is None:
                    continue
                self.store.mark_episode_queued(
                    episode["subscription_id"],
                    episode["episode_key"],
                    job_id=job["id"],
                    updated_at=_iso(self._clock()),
                )
            else:
                job_id = episode["job_id"]
                if job_id is not None:
                    try:
                        job = self._job_store.get(job_id)
                    except KeyError:
                        job = None

            terminal_state: str | None = None
            if job is not None and job["state"] == "done":
                terminal_state = "completed"
            elif job is not None and job["state"] in {"failed", "interrupted"}:
                terminal_state = "failed"
            elif job is None and episode["state"] == "queued":
                terminal_state = (
                    "completed" if self._library_has_source(episode["enclosure_url"]) else "failed"
                )
            if terminal_state is not None:
                updated_at = _iso(self._clock())
                if terminal_state == "completed" and self._email_outbox is not None:
                    self._email_outbox.record_subscription_completion(
                        episode, updated_at=updated_at
                    )
                else:
                    self.store.mark_episode_terminal(
                        episode["subscription_id"],
                        episode["episode_key"],
                        state=terminal_state,
                        updated_at=updated_at,
                    )

    class _Claim:
        def __init__(self, manager: SubscriptionManager, subscription_id: str) -> None:
            self._manager = manager
            self._subscription_id = subscription_id

        def __enter__(self) -> None:
            with self._manager._in_flight_condition:
                if self._manager._stopping.is_set():
                    raise PremiumFeatureUnavailableError("premium_feature_unavailable")
                if self._subscription_id in self._manager._in_flight:
                    raise FeedError("subscription poll is already in progress")
                self._manager._in_flight.add(self._subscription_id)

        def __exit__(self, *_args: object) -> None:
            with self._manager._in_flight_condition:
                self._manager._in_flight.discard(self._subscription_id)
                self._manager._in_flight_condition.notify_all()

    def _claim(self, subscription_id: str) -> _Claim:
        return self._Claim(self, subscription_id)

    @staticmethod
    def _jitter(subscription_id: str) -> int:
        digest = hashlib.sha256(subscription_id.encode()).digest()
        offset = int.from_bytes(digest[:2], "big") % 601 - 300
        return POLL_INTERVAL_SECONDS + offset

    def _run_scheduler(self) -> None:
        next_reconciliation = 0.0
        while not self._stopping.is_set() and not self._scheduler_stop.is_set():
            monotonic_now = time.monotonic()
            if monotonic_now >= next_reconciliation:
                try:
                    self._reconcile_jobs()
                except Exception:
                    logger.exception("Subscription job reconciliation failed; retrying later")
                next_reconciliation = monotonic_now + RECONCILIATION_INTERVAL_SECONDS
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
