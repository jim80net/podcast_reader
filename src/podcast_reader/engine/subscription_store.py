"""Private SQLite persistence for premium podcast subscriptions.

This database deliberately lives beside, rather than inside, the job journal.
Subscription migrations and backup/restore proofs therefore cannot change the
manual-submission path or its byte-level persistence contract.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, TypedDict

from podcast_reader.engine.settings import (
    ensure_owner_only_dir,
    ensure_windows_private_file,
    verify_windows_private_file,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SCHEMA_VERSION = 1
DATABASE_DIR = "subscriptions"
DATABASE_FILE = "subscriptions.sqlite3"


class SubscriptionRecord(TypedDict):
    id: str
    feed_url: str
    enabled: bool
    title: str | None
    normalized_origin: str
    etag: str | None
    last_modified: str | None
    last_checked_at: str | None
    next_check_at: str | None
    last_error: str | None
    last_error_at: str | None
    created_at: str
    updated_at: str


class EpisodeRecord(TypedDict):
    subscription_id: str
    episode_key: str
    guid: str | None
    enclosure_url: str
    title: str | None
    published_at: str | None
    state: str
    job_id: str | None
    created_at: str
    updated_at: str


class BackupProof(TypedDict):
    integrity_check: str
    row_counts: dict[str, int]
    schema_version: int


_SCHEMA = """
CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    feed_url TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    title TEXT,
    normalized_origin TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    last_checked_at TEXT,
    next_check_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE episodes (
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    episode_key TEXT NOT NULL,
    guid TEXT,
    enclosure_url TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    state TEXT NOT NULL CHECK (
        state IN ('baseline', 'discovered', 'queued', 'completed', 'failed')
    ),
    job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, episode_key)
);
CREATE INDEX subscriptions_due_idx ON subscriptions(enabled, next_check_at);
CREATE INDEX episodes_state_idx ON episodes(subscription_id, state);
"""


def _harden_file(path: Path) -> None:
    if sys.platform == "win32":  # pragma: no cover - Windows CI exercises helper tests
        if path.exists():
            ensure_windows_private_file(path)
            verify_windows_private_file(path)
        return
    if os.name == "posix" and path.exists():
        path.chmod(0o600)


def _row_to_subscription(row: sqlite3.Row) -> SubscriptionRecord:
    return SubscriptionRecord(
        id=row["id"],
        feed_url=row["feed_url"],
        enabled=bool(row["enabled"]),
        title=row["title"],
        normalized_origin=row["normalized_origin"],
        etag=row["etag"],
        last_modified=row["last_modified"],
        last_checked_at=row["last_checked_at"],
        next_check_at=row["next_check_at"],
        last_error=row["last_error"],
        last_error_at=row["last_error_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SubscriptionStore:
    """Thread-safe, independently migrated subscription database."""

    def __init__(self, data_dir: Path) -> None:
        directory = data_dir / DATABASE_DIR
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        ensure_owner_only_dir(directory)
        self.path = directory / DATABASE_FILE
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        # A single engine process owns this database. DELETE journaling avoids
        # leaving feed URLs in permission-sensitive WAL/SHM sidecars.
        self._connection.execute("PRAGMA journal_mode = DELETE")
        _harden_file(self.path)
        try:
            self._migrate()
        except Exception:
            self._connection.close()
            raise

    def _migrate(self) -> None:
        with self._transaction() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"subscription database schema {version} is newer than supported "
                    f"schema {SCHEMA_VERSION}"
                )
            if version == 0:
                connection.executescript(_SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported subscription database schema {version}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def list_subscriptions(self) -> list[SubscriptionRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM subscriptions ORDER BY created_at, id"
            ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    def get_subscription(self, subscription_id: str) -> SubscriptionRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()
        if row is None:
            raise KeyError(subscription_id)
        return _row_to_subscription(row)

    def insert_subscription(
        self,
        *,
        subscription_id: str,
        feed_url: str,
        title: str | None,
        normalized_origin: str,
        etag: str | None,
        last_modified: str | None,
        checked_at: str,
        next_check_at: str,
        baseline_episodes: list[EpisodeRecord],
    ) -> SubscriptionRecord:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO subscriptions (
                    id, feed_url, enabled, title, normalized_origin, etag, last_modified,
                    last_checked_at, next_check_at, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subscription_id,
                    feed_url,
                    title,
                    normalized_origin,
                    etag,
                    last_modified,
                    checked_at,
                    next_check_at,
                    checked_at,
                    checked_at,
                ),
            )
            self._insert_episodes(connection, baseline_episodes, state="baseline")
        return self.get_subscription(subscription_id)

    def delete_subscription(self, subscription_id: str) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM subscriptions WHERE id = ?", (subscription_id,)
            )
            if cursor.rowcount == 0:
                raise KeyError(subscription_id)

    def record_not_modified(
        self, subscription_id: str, *, checked_at: str, next_check_at: str
    ) -> SubscriptionRecord:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE subscriptions
                SET last_checked_at = ?, next_check_at = ?, last_error = NULL,
                    last_error_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (checked_at, next_check_at, checked_at, subscription_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(subscription_id)
        return self.get_subscription(subscription_id)

    def record_poll(
        self,
        subscription_id: str,
        *,
        title: str | None,
        etag: str | None,
        last_modified: str | None,
        checked_at: str,
        next_check_at: str,
        episodes: list[EpisodeRecord],
    ) -> tuple[SubscriptionRecord, int]:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE subscriptions
                SET title = COALESCE(?, title), etag = ?, last_modified = ?,
                    last_checked_at = ?, next_check_at = ?, last_error = NULL,
                    last_error_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    etag,
                    last_modified,
                    checked_at,
                    next_check_at,
                    checked_at,
                    subscription_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(subscription_id)
            inserted = self._insert_episodes(connection, episodes, state="discovered")
        return self.get_subscription(subscription_id), inserted

    @staticmethod
    def _insert_episodes(
        connection: sqlite3.Connection,
        episodes: list[EpisodeRecord],
        *,
        state: str,
    ) -> int:
        inserted = 0
        for episode in episodes:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO episodes (
                    subscription_id, episode_key, guid, enclosure_url, title,
                    published_at, state, job_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode["subscription_id"],
                    episode["episode_key"],
                    episode["guid"],
                    episode["enclosure_url"],
                    episode["title"],
                    episode["published_at"],
                    state,
                    episode["job_id"],
                    episode["created_at"],
                    episode["updated_at"],
                ),
            )
            inserted += cursor.rowcount
        return inserted

    def record_error(
        self,
        subscription_id: str,
        *,
        checked_at: str,
        next_check_at: str,
        detail: str,
    ) -> None:
        bounded = detail[:240]
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE subscriptions
                SET last_checked_at = ?, next_check_at = ?, last_error = ?,
                    last_error_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (checked_at, next_check_at, bounded, checked_at, checked_at, subscription_id),
            )

    def due_subscription_ids(self, now: str, *, limit: int) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id FROM subscriptions
                WHERE enabled = 1 AND next_check_at IS NOT NULL AND next_check_at <= ?
                ORDER BY next_check_at, id LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [row["id"] for row in rows]

    def accelerate_checks(self, next_check_at: str) -> None:
        """Move enabled feeds up after capability enable/start, never delay them."""
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE subscriptions
                SET next_check_at = ?, updated_at = ?
                WHERE enabled = 1
                  AND (next_check_at IS NULL OR next_check_at > ?)
                """,
                (next_check_at, next_check_at, next_check_at),
            )

    def list_episodes(self, subscription_id: str) -> list[EpisodeRecord]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM episodes WHERE subscription_id = ?
                ORDER BY created_at, episode_key
                """,
                (subscription_id,),
            ).fetchall()
        return [
            EpisodeRecord(
                subscription_id=row["subscription_id"],
                episode_key=row["episode_key"],
                guid=row["guid"],
                enclosure_url=row["enclosure_url"],
                title=row["title"],
                published_at=row["published_at"],
                state=row["state"],
                job_id=row["job_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def backup_and_verify(self, output: Path) -> BackupProof:
        """Create an online backup and prove integrity plus application row counts."""
        if output.resolve() == self.path.resolve():
            raise ValueError("subscription backup output must differ from the live database")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        ensure_owner_only_dir(output.parent)
        with self._lock, sqlite3.connect(output) as destination:
            self._connection.backup(destination)
            source_counts = self._row_counts(self._connection)
        _harden_file(output)
        uri = f"file:{output.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as restored:
            integrity = str(restored.execute("PRAGMA integrity_check").fetchone()[0])
            version = int(restored.execute("PRAGMA user_version").fetchone()[0])
            restored_counts = self._row_counts(restored)
        if integrity != "ok" or version != SCHEMA_VERSION or restored_counts != source_counts:
            raise RuntimeError("subscription backup verification failed")
        return BackupProof(
            integrity_check=integrity,
            row_counts=restored_counts,
            schema_version=version,
        )

    @staticmethod
    def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("subscriptions", "episodes")
        }

    def raw_connection_for_tests(self) -> sqlite3.Connection:
        """Return the connection for focused migration tests; never used by production."""
        return self._connection


def episode_record(
    *,
    subscription_id: str,
    episode_key: str,
    guid: str | None,
    enclosure_url: str,
    title: str | None,
    published_at: str | None,
    now: str,
) -> EpisodeRecord:
    return EpisodeRecord(
        subscription_id=subscription_id,
        episode_key=episode_key,
        guid=guid,
        enclosure_url=enclosure_url,
        title=title,
        published_at=published_at,
        state="discovered",
        job_id=None,
        created_at=now,
        updated_at=now,
    )
