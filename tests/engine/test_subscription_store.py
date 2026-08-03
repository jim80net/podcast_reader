from __future__ import annotations

import os
import sqlite3
import stat
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.subscription_store import (
    SCHEMA_VERSION,
    SubscriptionStore,
    episode_record,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_database_is_independent_and_owner_only(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    try:
        assert store.path == tmp_path / "subscriptions" / "subscriptions.sqlite3"
        if os.name == "posix":
            assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert not (tmp_path / "jobs.json").exists()
        assert (
            store.raw_connection_for_tests().execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
    finally:
        store.close()


def test_newer_schema_fails_closed_without_modifying_it(tmp_path: Path) -> None:
    directory = tmp_path / "subscriptions"
    directory.mkdir()
    path = directory / "subscriptions.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="newer than supported"):
        SubscriptionStore(tmp_path)
    assert path.read_bytes() == before


def test_version_one_migration_normalizes_legacy_published_timestamps(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    now = "2026-08-03T00:00:00Z"
    store.insert_subscription(
        subscription_id="sub_legacy",
        feed_url="https://example.com/feed.xml",
        title="Legacy",
        normalized_origin="https://example.com",
        etag=None,
        last_modified=None,
        checked_at=now,
        next_check_at=now,
        baseline_episodes=[
            episode_record(
                subscription_id="sub_legacy",
                episode_key="later",
                guid="later",
                enclosure_url="https://example.com/later.mp3",
                title="Later",
                published_at="2024-01-01T00:00:00.500000Z",
                now=now,
            ),
            episode_record(
                subscription_id="sub_legacy",
                episode_key="earlier",
                guid="earlier",
                enclosure_url="https://example.com/earlier.mp3",
                title="Earlier",
                published_at="Mon, 01 Jan 2024 00:00:00 GMT",
                now=now,
            ),
        ],
    )
    connection = store.raw_connection_for_tests()
    connection.execute("UPDATE episodes SET state = 'discovered'")
    connection.execute(
        "UPDATE episodes SET published_at = ? WHERE episode_key = ?",
        ("Mon, 01 Jan 2024 00:00:00 GMT", "earlier"),
    )
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    store.close()

    migrated = SubscriptionStore(tmp_path)
    try:
        assert migrated.raw_connection_for_tests().execute("PRAGMA user_version").fetchone()[0] == 2
        episodes = migrated.discovered_episodes("sub_legacy", limit=2)
        assert [episode["episode_key"] for episode in episodes] == ["earlier", "later"]
        assert [episode["published_at"] for episode in episodes] == [
            "2024-01-01T00:00:00.000000Z",
            "2024-01-01T00:00:00.500000Z",
        ]
    finally:
        migrated.close()


def test_online_backup_is_restore_proved_with_exact_row_counts(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    try:
        proof = store.backup_and_verify(tmp_path / "backups" / "subscriptions.sqlite3")
        assert proof == {
            "integrity_check": "ok",
            "row_counts": {"subscriptions": 0, "episodes": 0},
            "schema_version": SCHEMA_VERSION,
        }
        output = tmp_path / "backups" / "subscriptions.sqlite3"
        if os.name == "posix":
            assert stat.S_IMODE(output.stat().st_mode) == 0o600
            assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    finally:
        store.close()
