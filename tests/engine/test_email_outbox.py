from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine import library
from podcast_reader.engine.email_outbox import (
    EMAIL_BACKOFF_MAX_SECONDS,
    EMAIL_CONTENT_MAX_BYTES,
    EmailCapabilitySnapshot,
    EmailFeatureUnavailableError,
    EmailOutboxError,
    EmailOutboxManager,
)
from podcast_reader.engine.subscription_store import SubscriptionStore, episode_record
from podcast_reader.types import LibraryEntry

if TYPE_CHECKING:
    from collections.abc import Iterator

UTC = timezone.utc
SUBJECT = "usr_email_tests"
OTHER_SUBJECT = "usr_other_account"
ACTION_ID = "act_AAAAAAAAAAAAAAAAAAAAAAAA"
DELIVERY_ID = "del_BBBBBBBBBBBBBBBBBBBBBBBB"


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: float) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 8, 3, 4, 0, tzinfo=UTC))


@pytest.fixture
def outbox(
    tmp_path: Path, clock: MutableClock
) -> Iterator[tuple[EmailOutboxManager, SubscriptionStore, Path]]:
    store = SubscriptionStore(tmp_path / "data")
    library_dir = tmp_path / "library"
    manager = EmailOutboxManager(store, library_dir=lambda: library_dir, clock=clock)
    try:
        yield manager, store, library_dir
    finally:
        store.close()


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _capability(
    clock: MutableClock,
    *,
    subject: str = SUBJECT,
    enabled: bool = True,
    lifetime_seconds: int = 300,
) -> EmailCapabilitySnapshot:
    return EmailCapabilitySnapshot(
        schema_version=1,
        subject=subject,
        entitlement_revision=4,
        flags_revision=9,
        transcript_email=enabled,
        expires_at=_iso(clock.value + timedelta(seconds=lifetime_seconds)),
    )


def _seed_library(library_dir: Path, source: str = "https://example.com/episode.mp3") -> str:
    source_id = library.source_identity(source)
    directory = library.entry_dir(library_dir, source_id)
    directory.mkdir(parents=True)
    transcript_path = directory / "episode.json"
    transcript_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0, "text": "  Cafe\u0301 opening  "},
                    {"start": 65.9, "text": "Second line"},
                ]
            }
        ),
        encoding="utf-8",
    )
    library.add_entry(
        library_dir,
        LibraryEntry(
            source_id=source_id,
            source=source,
            title="  Fixture episode  ",
            html_path=str(directory / "episode.html"),
            created_at=1_700_000_000.0,
        ),
    )
    return source_id


def _seed_subscription(
    store: SubscriptionStore,
    clock: MutableClock,
    *,
    source: str = "https://example.com/episode.mp3",
) -> None:
    now = _iso(clock.value)
    store.insert_subscription(
        subscription_id="sub_email",
        feed_url="https://example.com/feed.xml",
        title="Email feed",
        normalized_origin="https://example.com",
        etag=None,
        last_modified=None,
        checked_at=now,
        next_check_at=now,
        baseline_episodes=[],
    )
    store.record_poll(
        "sub_email",
        title=None,
        etag=None,
        last_modified=None,
        checked_at=now,
        next_check_at=now,
        episodes=[
            episode_record(
                subscription_id="sub_email",
                episode_key="episode-1",
                guid="guid-1",
                enclosure_url=source,
                title="Fixture episode",
                published_at=now,
                now=now,
            )
        ],
    )
    store.mark_episode_queued("sub_email", "episode-1", job_id="job_1", updated_at=now)


def test_capability_is_memory_only_fail_closed_and_subject_scoped(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, store, library_dir = outbox
    source_id = _seed_library(library_dir)

    with pytest.raises(EmailFeatureUnavailableError):
        manager.create_manual(action_id=ACTION_ID, source_id=source_id)
    manager.update_capability(_capability(clock, enabled=False))
    assert not manager.is_available()
    manager.update_capability(_capability(clock))
    assert manager.is_available()
    manager.create_manual(action_id=ACTION_ID, source_id=source_id)

    restarted = EmailOutboxManager(store, library_dir=lambda: library_dir, clock=clock)
    assert not restarted.is_available()
    with pytest.raises(EmailFeatureUnavailableError):
        restarted.claim()

    manager.update_capability(_capability(clock, subject=OTHER_SUBJECT))
    assert manager.claim() is None
    manager.update_capability(_capability(clock))
    clock.advance(minutes=6)
    assert not manager.is_available()
    with pytest.raises(EmailFeatureUnavailableError):
        manager.claim()


@pytest.mark.parametrize(
    "snapshot",
    [
        EmailCapabilitySnapshot(2, SUBJECT, 1, 1, True, "2026-08-03T04:05:00Z"),
        EmailCapabilitySnapshot(1, "bad", 1, 1, True, "2026-08-03T04:05:00Z"),
        EmailCapabilitySnapshot(1, SUBJECT, -1, 1, True, "2026-08-03T04:05:00Z"),
        EmailCapabilitySnapshot(1, SUBJECT, 1, 1, True, "2026-08-03T04:11:00Z"),
    ],
)
def test_capability_rejects_invalid_or_overlong_snapshots(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], snapshot: EmailCapabilitySnapshot
) -> None:
    manager, _, _ = outbox
    with pytest.raises(ValueError):
        manager.update_capability(snapshot)


def test_subscription_consent_has_no_backfill_and_completion_is_atomic_and_idempotent(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, store, library_dir = outbox
    _seed_library(library_dir)
    _seed_subscription(store, clock)

    episode = store.episodes_for_reconciliation()[0]
    assert manager.record_subscription_completion(episode, updated_at=_iso(clock.value)) is None
    assert store.list_email_outbox() == []

    manager.update_capability(_capability(clock))
    clock.advance(seconds=1)
    manager.set_subscription_preference("sub_email", subject=SUBJECT, enabled=True)
    manager.update_capability(_capability(clock))
    assert manager.record_subscription_completion(episode, updated_at=_iso(clock.value)) is None
    assert manager.list_status() == []

    # A newly completed item after consent produces exactly one durable row.
    store.raw_connection_for_tests().execute(
        "UPDATE episodes SET state = 'queued', job_id = 'job_2' WHERE episode_key = 'episode-1'"
    )
    store.raw_connection_for_tests().commit()
    new_episode = store.episodes_for_reconciliation()[0]
    delivery_id = manager.record_subscription_completion(new_episode, updated_at=_iso(clock.value))
    assert delivery_id is not None
    assert manager.record_subscription_completion(new_episode, updated_at=_iso(clock.value)) is None
    assert [item["client_delivery_id"] for item in manager.list_status()] == [delivery_id]


def test_capability_refresh_recovers_consent_covered_completion_after_restart(
    tmp_path: Path, clock: MutableClock
) -> None:
    data_dir = tmp_path / "data"
    library_dir = tmp_path / "library"
    store = SubscriptionStore(data_dir)
    manager = EmailOutboxManager(store, library_dir=lambda: library_dir, clock=clock)
    _seed_library(library_dir)
    _seed_subscription(store, clock)
    manager.update_capability(_capability(clock))
    manager.set_subscription_preference("sub_email", subject=SUBJECT, enabled=True)
    manager.clear_capability()

    episode = store.episodes_for_reconciliation()[0]
    manager.record_subscription_completion(episode, updated_at=_iso(clock.value))
    assert store.list_email_outbox() == []
    store.close()

    restarted_store = SubscriptionStore(data_dir)
    restarted = EmailOutboxManager(restarted_store, library_dir=lambda: library_dir, clock=clock)
    try:
        restarted.update_capability(_capability(clock))
        items = restarted_store.list_email_outbox()
        assert len(items) == 1
        assert items[0]["job_id"] == "job_1"
        restarted.update_capability(_capability(clock))
        assert len(restarted_store.list_email_outbox()) == 1
    finally:
        restarted_store.close()


def test_manual_action_is_idempotent_and_claim_materializes_only_bounded_content(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, store, library_dir = outbox
    source_id = _seed_library(library_dir)
    manager.update_capability(_capability(clock))

    first = manager.create_manual(action_id=ACTION_ID, source_id=source_id)
    second = manager.create_manual(action_id=ACTION_ID, source_id=source_id)
    assert second == first
    claim = manager.claim()
    assert claim is not None
    assert claim["title"] == "Fixture episode"
    assert claim["transcript_text"] == "00:00 Café opening\n01:05 Second line\n"
    assert claim["content_sha256"] == hashlib.sha256(claim["transcript_text"].encode()).hexdigest()
    assert set(claim) == {
        "schema_version",
        "client_delivery_id",
        "claim_generation",
        "consent_kind",
        "title",
        "transcript_text",
        "content_sha256",
    }

    columns = {
        row[1]
        for row in store.raw_connection_for_tests().execute("PRAGMA table_info(email_outbox)")
    }
    assert columns.isdisjoint({"title", "transcript", "transcript_text", "feed_url", "email"})


def test_manual_action_reuse_for_different_source_is_an_idempotency_conflict(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, _, library_dir = outbox
    first_source = _seed_library(library_dir)
    second_source = _seed_library(library_dir, "https://example.com/different.mp3")
    manager.update_capability(_capability(clock))
    manager.create_manual(action_id=ACTION_ID, source_id=first_source)

    with pytest.raises(EmailOutboxError) as caught:
        manager.create_manual(action_id=ACTION_ID, source_id=second_source)
    assert caught.value.code == "idempotency_conflict"


def test_status_is_scoped_to_the_current_capability_subject(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, _, library_dir = outbox
    source_id = _seed_library(library_dir)
    manager.update_capability(_capability(clock))
    first = manager.create_manual(action_id=ACTION_ID, source_id=source_id)
    manager.update_capability(_capability(clock, subject=OTHER_SUBJECT))
    second = manager.create_manual(action_id=ACTION_ID, source_id=source_id)

    assert manager.list_status() == [second]
    manager.update_capability(_capability(clock, subject=SUBJECT, enabled=False))
    assert manager.list_status() == [first]


def test_claim_lease_recovery_completion_replay_and_conflict(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, _, library_dir = outbox
    source_id = _seed_library(library_dir)
    manager.update_capability(_capability(clock))
    created = manager.create_manual(action_id=ACTION_ID, source_id=source_id)

    first = manager.claim()
    assert first is not None
    assert manager.claim() is None
    clock.advance(seconds=31)
    second = manager.claim()
    assert second is not None
    assert second["client_delivery_id"] == first["client_delivery_id"]
    assert second["client_delivery_id"] == created["client_delivery_id"]
    assert second["claim_generation"] == first["claim_generation"] + 1

    delivered_at = _iso(clock.value)
    completed = manager.complete(
        client_delivery_id=second["client_delivery_id"],
        claim_generation=second["claim_generation"],
        delivery_id=DELIVERY_ID,
        delivered_at=delivered_at,
    )
    replay = manager.complete(
        client_delivery_id=second["client_delivery_id"],
        claim_generation=second["claim_generation"],
        delivery_id=DELIVERY_ID,
        delivered_at=delivered_at,
    )
    assert replay == completed
    with pytest.raises(RuntimeError, match="conflicts"):
        manager.complete(
            client_delivery_id=second["client_delivery_id"],
            claim_generation=second["claim_generation"],
            delivery_id="del_CCCCCCCCCCCCCCCCCCCCCCCC",
            delivered_at=delivered_at,
        )


def test_retry_backoff_caps_and_premium_pause_does_not_consume_attempt(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, store, library_dir = outbox
    source_id = _seed_library(library_dir)
    manager.update_capability(_capability(clock, lifetime_seconds=600))
    manager.create_manual(action_id=ACTION_ID, source_id=source_id)

    first = manager.claim()
    assert first is not None
    paused = manager.release(
        client_delivery_id=first["client_delivery_id"],
        claim_generation=first["claim_generation"],
        error_code="premium_feature_unavailable",
    )
    assert paused["attempts"] == 0
    assert paused["state"] == "pending"

    saw_cap = False
    for expected_attempt in range(1, 9):
        manager.update_capability(_capability(clock, lifetime_seconds=600))
        claim = manager.claim()
        assert claim is not None
        released = manager.release(
            client_delivery_id=claim["client_delivery_id"],
            claim_generation=claim["claim_generation"],
            error_code="delivery_unavailable",
        )
        assert released["attempts"] == expected_attempt
        if expected_attempt < 8:
            row = store.list_email_outbox()[0]
            expected_delay = min(60 * (4 ** (expected_attempt - 1)), EMAIL_BACKOFF_MAX_SECONDS)
            saw_cap = saw_cap or expected_delay == EMAIL_BACKOFF_MAX_SECONDS
            assert row["next_attempt_at"] == _iso(clock.value + timedelta(seconds=expected_delay))
            clock.advance(seconds=expected_delay)
        else:
            assert released["state"] == "failed"
            assert store.list_email_outbox()[0]["next_attempt_at"] is None
    assert saw_cap


def test_revocation_cancels_pending_but_allows_claimed_completion(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, store, library_dir = outbox
    _seed_library(library_dir)
    _seed_subscription(store, clock)
    manager.update_capability(_capability(clock))
    manager.set_subscription_preference("sub_email", subject=SUBJECT, enabled=True)
    episode = store.episodes_for_reconciliation()[0]
    manager.record_subscription_completion(episode, updated_at=_iso(clock.value))
    claimed = manager.claim()
    assert claimed is not None

    manager.set_subscription_preference("sub_email", subject=SUBJECT, enabled=False)
    completed = manager.complete(
        client_delivery_id=claimed["client_delivery_id"],
        claim_generation=claimed["claim_generation"],
        delivery_id=DELIVERY_ID,
        delivered_at=_iso(clock.value),
    )
    assert completed["state"] == "delivered"

    # Re-enabling creates a new consent revision; disabling cancels its pending work.
    manager.set_subscription_preference("sub_email", subject=SUBJECT, enabled=True)
    store.raw_connection_for_tests().execute(
        "UPDATE episodes SET state = 'queued', job_id = 'job_3' WHERE episode_key = 'episode-1'"
    )
    store.raw_connection_for_tests().commit()
    manager.record_subscription_completion(
        store.episodes_for_reconciliation()[0], updated_at=_iso(clock.value)
    )
    manager.set_subscription_preference("sub_email", subject=SUBJECT, enabled=False)
    assert {item["state"] for item in manager.list_status()} == {"cancelled", "delivered"}


def test_oversized_transcript_fails_terminally_without_returning_content(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path], clock: MutableClock
) -> None:
    manager, store, library_dir = outbox
    source_id = _seed_library(library_dir)
    directory = library.entry_dir(library_dir, source_id)
    (directory / "episode.json").write_text(
        json.dumps({"segments": [{"start": 0, "text": "x" * (EMAIL_CONTENT_MAX_BYTES + 1)}]}),
        encoding="utf-8",
    )
    manager.update_capability(_capability(clock))
    manager.create_manual(action_id=ACTION_ID, source_id=source_id)

    with pytest.raises(EmailOutboxError) as caught:
        manager.claim()
    assert caught.value.code == "delivery_too_large"
    item = store.list_email_outbox()[0]
    assert item["state"] == "failed"
    assert item["error_code"] == "delivery_too_large"


@pytest.mark.parametrize("start", [float("nan"), float("inf"), float("-inf"), True])
def test_non_finite_or_boolean_timestamp_fails_as_stable_artifact_error(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path],
    clock: MutableClock,
    start: float | bool,
) -> None:
    manager, store, library_dir = outbox
    source_id = _seed_library(library_dir)
    directory = library.entry_dir(library_dir, source_id)
    (directory / "episode.json").write_text(
        json.dumps({"segments": [{"start": start, "text": "invalid timestamp"}]}),
        encoding="utf-8",
    )
    manager.update_capability(_capability(clock))
    manager.create_manual(action_id=ACTION_ID, source_id=source_id)

    with pytest.raises(EmailOutboxError) as caught:
        manager.claim()
    assert caught.value.code == "artifact_unavailable"
    item = store.list_email_outbox()[0]
    assert item["state"] == "failed"
    assert item["attempts"] == 1
    assert item["error_code"] == "artifact_unavailable"


def test_library_move_fails_once_with_distinct_terminal_artifact_error(
    outbox: tuple[EmailOutboxManager, SubscriptionStore, Path],
    clock: MutableClock,
    tmp_path: Path,
) -> None:
    manager, store, library_dir = outbox
    source_id = _seed_library(library_dir)
    manager.update_capability(_capability(clock))
    manager.create_manual(action_id=ACTION_ID, source_id=source_id)
    moved = EmailOutboxManager(store, library_dir=lambda: tmp_path / "new-library", clock=clock)
    moved.update_capability(_capability(clock))

    with pytest.raises(EmailOutboxError) as caught:
        moved.claim()
    assert caught.value.code == "artifact_unavailable"
    item = store.list_email_outbox()[0]
    assert item["state"] == "failed"
    assert item["attempts"] == 1
    assert item["next_attempt_at"] is None


def test_frozen_email_contract_fixtures_have_exact_v1_shapes() -> None:
    root = Path("src/podcast_reader/engine/contracts/v1/email")
    expected = {
        "online-capability.json": {
            "schema_version",
            "subject",
            "entitlement_revision",
            "flags_revision",
            "transcript_email",
            "expires_at",
        },
        "claim.json": {
            "schema_version",
            "client_delivery_id",
            "claim_generation",
            "consent_kind",
            "title",
            "transcript_text",
            "content_sha256",
        },
        "completion.json": {
            "schema_version",
            "client_delivery_id",
            "claim_generation",
            "delivery_id",
            "delivered_at",
        },
        "release.json": {
            "schema_version",
            "client_delivery_id",
            "claim_generation",
            "error_code",
        },
        "manual-create.json": {"schema_version", "action_id", "source_id"},
    }
    for filename, keys in expected.items():
        fixture = json.loads((root / filename).read_text(encoding="utf-8"))
        assert set(fixture) == keys
        assert fixture["schema_version"] == 1
