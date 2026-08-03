from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.subscription_feed import FeedResponse, FeedTemporaryError
from podcast_reader.engine.subscription_store import SubscriptionStore
from podcast_reader.engine.subscriptions import (
    OnlineCapabilitySnapshot,
    PremiumFeatureUnavailableError,
    SubscriptionManager,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_FEED_BASELINE = b"""
<rss><channel><title>Fixture Show</title>
  <item><guid>old</guid><title>Old</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/old.mp3"/></item>
</channel></rss>
"""
_FEED_UPDATED = b"""
<rss><channel><title>Fixture Show</title>
  <item><guid>new</guid><title>New</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/new.mp3"/></item>
  <item><guid>old</guid><title>Old</title>
    <enclosure type="audio/mpeg" url="https://93.184.216.34/old.mp3"/></item>
</channel></rss>
"""


@dataclass
class _Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


class _Fetcher:
    def __init__(self) -> None:
        self.responses = [
            FeedResponse(
                200,
                "https://93.184.216.34/show.xml",
                _FEED_BASELINE,
                '"v1"',
                "Mon, 01 Jan 2024 00:00:00 GMT",
            )
        ]
        self.requests: list[tuple[str | None, str | None]] = []
        self.called = threading.Event()

    def fetch(
        self,
        _url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> FeedResponse:
        assert should_continue()
        self.requests.append((etag, last_modified))
        self.called.set()
        return self.responses.pop(0)


def _snapshot(clock: _Clock, *, enabled: bool = True) -> OnlineCapabilitySnapshot:
    return OnlineCapabilitySnapshot(
        schema_version=1,
        subject="usr_test_01",
        entitlement_revision=7,
        flags_revision=12,
        podcast_subscriptions=enabled,
        expires_at=(clock.now + timedelta(minutes=5)).isoformat(),
    )


def test_frozen_online_capability_fixture_has_exact_shape() -> None:
    path = (
        Path(__file__).parents[2]
        / "src/podcast_reader/engine/contracts/v1/subscriptions/online-capability.json"
    )
    fixture = json.loads(path.read_text())
    assert set(fixture) == {
        "schema_version",
        "subject",
        "entitlement_revision",
        "flags_revision",
        "podcast_subscriptions",
        "expires_at",
    }
    OnlineCapabilitySnapshot(**fixture)


def test_local_and_free_states_preserve_reads_but_gate_mutation(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher(), clock=clock)
    try:
        manager.start()
        assert manager._thread is None
        assert manager.list_subscriptions() == []
        with pytest.raises(PremiumFeatureUnavailableError, match="premium_feature_unavailable"):
            manager.create_subscription("https://93.184.216.34/show.xml")
        manager.update_capability(_snapshot(clock, enabled=False))
        assert manager._thread is None
        assert manager.list_subscriptions() == []
        with pytest.raises(PremiumFeatureUnavailableError):
            manager.create_subscription("https://93.184.216.34/show.xml")
    finally:
        manager.shutdown()


def test_live_capability_identity_and_revisions_never_enter_sqlite(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher(), clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        manager.create_subscription("https://93.184.216.34/show.xml")
        database_bytes = manager.store.path.read_bytes()
        assert b"usr_test_01" not in database_bytes
        assert b"entitlement_revision" not in database_bytes
        assert b"flags_revision" not in database_bytes
        assert b"podcast_subscriptions" not in database_bytes
    finally:
        manager.shutdown()


def test_initial_subscribe_baselines_archive_then_poll_discovers_only_new_episode(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    fetcher = _Fetcher()
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=fetcher, clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        subscription = manager.create_subscription("https://93.184.216.34/show.xml")
        baseline = manager.store.list_episodes(subscription["id"])
        assert [(item["episode_key"], item["state"]) for item in baseline] == [("old", "baseline")]

        fetcher.responses.append(
            FeedResponse(
                200,
                "https://93.184.216.34/show.xml",
                _FEED_UPDATED,
                '"v2"',
                "Tue, 02 Jan 2024 00:00:00 GMT",
            )
        )
        clock.now += timedelta(minutes=1)
        result = manager.poll_subscription(subscription["id"])
        assert result.discovered_count == 1
        assert fetcher.requests[-1] == ('"v1"', "Mon, 01 Jan 2024 00:00:00 GMT")
        assert [
            (item["episode_key"], item["state"])
            for item in manager.store.list_episodes(subscription["id"])
        ] == [("old", "baseline"), ("new", "discovered")]
    finally:
        manager.shutdown()


def test_reordered_feed_is_deduplicated_and_304_changes_no_episodes(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    fetcher = _Fetcher()
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=fetcher, clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        subscription = manager.create_subscription("https://93.184.216.34/show.xml")
        fetcher.responses.extend(
            [
                FeedResponse(200, subscription["feed_url"], _FEED_BASELINE, '"v2"', None),
                FeedResponse(304, subscription["feed_url"], b"", '"v2"', None),
            ]
        )
        assert manager.poll_subscription(subscription["id"]).discovered_count == 0
        assert manager.poll_subscription(subscription["id"]).not_modified is True
        assert len(manager.store.list_episodes(subscription["id"])) == 1
    finally:
        manager.shutdown()


def test_capability_expiry_and_restart_fail_closed_without_deleting_data(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher(), clock=clock)
    manager.update_capability(_snapshot(clock))
    subscription = manager.create_subscription("https://93.184.216.34/show.xml")
    clock.now += timedelta(minutes=6)
    assert manager.is_available() is False
    with pytest.raises(PremiumFeatureUnavailableError):
        manager.delete_subscription(subscription["id"])
    manager.shutdown()

    restarted = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher(), clock=clock)
    try:
        assert restarted.is_available() is False
        assert [item["id"] for item in restarted.list_subscriptions()] == [subscription["id"]]
    finally:
        restarted.shutdown()


def test_scheduler_polls_due_feed_only_while_capability_is_fresh(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    fetcher = _Fetcher()
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=fetcher, clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        subscription = manager.create_subscription("https://93.184.216.34/show.xml")
        fetcher.called.clear()
        fetcher.responses.append(FeedResponse(304, subscription["feed_url"], b"", '"v1"', None))
        clock.now += timedelta(minutes=31)
        manager.update_capability(_snapshot(clock))
        clock.now += timedelta(seconds=16)
        manager.start()
        assert fetcher.called.wait(timeout=2)
        assert len(fetcher.requests) == 2

        manager.clear_capability()
        fetcher.called.clear()
        clock.now += timedelta(hours=1)
        manager._wake.set()
        assert fetcher.called.wait(timeout=0.2) is False
        assert len(fetcher.requests) == 2
    finally:
        manager.shutdown()


def test_invalid_capability_cannot_replace_live_snapshot(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher(), clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        invalid = OnlineCapabilitySnapshot(
            schema_version=2,
            subject="usr_test_01",
            entitlement_revision=7,
            flags_revision=12,
            podcast_subscriptions=True,
            expires_at=(clock.now + timedelta(minutes=5)).isoformat(),
        )
        with pytest.raises(ValueError, match="unsupported"):
            manager.update_capability(invalid)
        assert manager.is_available() is True
    finally:
        manager.shutdown()


def test_routine_capability_refresh_does_not_collapse_thirty_minute_poll_cadence(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=_Fetcher(), clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        subscription = manager.create_subscription("https://93.184.216.34/show.xml")
        scheduled = subscription["next_check_at"]
        clock.now += timedelta(minutes=1)
        refreshed = _snapshot(clock)
        manager.update_capability(
            replace(refreshed, entitlement_revision=refreshed.entitlement_revision + 1)
        )
        assert manager.store.get_subscription(subscription["id"])["next_check_at"] == scheduled
    finally:
        manager.shutdown()


def test_retry_after_is_persisted_within_the_bounded_schedule(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    fetcher = _Fetcher()
    manager = SubscriptionManager(SubscriptionStore(tmp_path), fetcher=fetcher, clock=clock)
    try:
        manager.update_capability(_snapshot(clock))
        subscription = manager.create_subscription("https://93.184.216.34/show.xml")

        class TemporarilyUnavailable:
            def fetch(
                self,
                _url: str,
                *,
                etag: str | None = None,
                last_modified: str | None = None,
                should_continue: Callable[[], bool] = lambda: True,
            ) -> FeedResponse:
                raise FeedTemporaryError(24 * 60 * 60)

        manager._fetcher = TemporarilyUnavailable()
        with pytest.raises(FeedTemporaryError):
            manager.poll_subscription(subscription["id"])
        updated = manager.store.get_subscription(subscription["id"])
        assert updated["last_error"] == "feed temporarily unavailable"
        assert updated["next_check_at"] == "2026-08-04T00:00:00Z"
    finally:
        manager.shutdown()


def test_capability_loss_during_fetch_cancels_without_persisting_feed_error(
    tmp_path: Path,
) -> None:
    clock = _Clock(datetime(2026, 8, 3, tzinfo=timezone.utc))
    baseline_fetcher = _Fetcher()
    manager = SubscriptionManager(
        SubscriptionStore(tmp_path), fetcher=baseline_fetcher, clock=clock
    )
    try:
        manager.update_capability(_snapshot(clock))
        subscription = manager.create_subscription("https://93.184.216.34/show.xml")

        class CapabilityDroppingFetcher:
            def fetch(
                self,
                _url: str,
                *,
                etag: str | None = None,
                last_modified: str | None = None,
                should_continue: Callable[[], bool] = lambda: True,
            ) -> FeedResponse:
                manager.clear_capability()
                assert should_continue() is False
                raise FeedTemporaryError(900)

        manager._fetcher = CapabilityDroppingFetcher()
        with pytest.raises(PremiumFeatureUnavailableError):
            manager.poll_subscription(subscription["id"])
        unchanged = manager.store.get_subscription(subscription["id"])
        assert unchanged["last_error"] is None
        assert unchanged["last_error_at"] is None
    finally:
        manager.shutdown()
