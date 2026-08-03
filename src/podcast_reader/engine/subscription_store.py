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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, TypedDict

from podcast_reader.engine.settings import (
    ensure_owner_only_dir,
    ensure_windows_private_file,
    verify_windows_private_file,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SCHEMA_VERSION = 3
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


class EmailPreferenceRecord(TypedDict):
    subscription_id: str
    subject: str
    enabled_at: str
    consent_revision: int
    disabled_at: str | None
    updated_at: str


class EmailOutboxRecord(TypedDict):
    client_delivery_id: str
    subject: str
    source_id: str
    job_id: str | None
    subscription_id: str | None
    consent_kind: str
    consent_revision: int
    manual_action_id: str | None
    state: str
    attempts: int
    next_attempt_at: str | None
    error_code: str | None
    claim_generation: int
    claimed_at: str | None
    claim_expires_at: str | None
    server_delivery_id: str | None
    created_at: str
    updated_at: str
    delivered_at: str | None


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
CREATE TABLE subscription_email_preferences (
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    enabled_at TEXT NOT NULL,
    consent_revision INTEGER NOT NULL CHECK (consent_revision >= 1),
    disabled_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, subject)
);
CREATE TABLE email_outbox (
    client_delivery_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    source_id TEXT NOT NULL CHECK (length(source_id) = 64),
    job_id TEXT,
    subscription_id TEXT REFERENCES subscriptions(id) ON DELETE SET NULL,
    consent_kind TEXT NOT NULL CHECK (
        consent_kind IN ('subscription_completion', 'manual')
    ),
    consent_revision INTEGER NOT NULL CHECK (consent_revision >= 1),
    manual_action_id TEXT,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'claimed', 'delivered', 'failed', 'cancelled')
    ),
    attempts INTEGER NOT NULL CHECK (attempts BETWEEN 0 AND 8),
    next_attempt_at TEXT,
    error_code TEXT CHECK (
        error_code IS NULL OR error_code IN (
            'premium_feature_unavailable', 'delivery_too_large',
            'idempotency_conflict', 'delivery_unavailable', 'email_not_verified'
        )
    ),
    claim_generation INTEGER NOT NULL CHECK (claim_generation >= 0),
    claimed_at TEXT,
    claim_expires_at TEXT,
    server_delivery_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    CHECK (
        (consent_kind = 'subscription_completion'
            AND job_id IS NOT NULL AND manual_action_id IS NULL) OR
        (consent_kind = 'manual' AND manual_action_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX email_outbox_automatic_unique
    ON email_outbox(subscription_id, job_id, consent_revision)
    WHERE consent_kind = 'subscription_completion';
CREATE UNIQUE INDEX email_outbox_manual_unique
    ON email_outbox(subject, manual_action_id)
    WHERE consent_kind = 'manual';
CREATE INDEX email_outbox_claim_idx
    ON email_outbox(subject, state, next_attempt_at, claim_expires_at, created_at);
"""

_EMAIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscription_email_preferences (
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    enabled_at TEXT NOT NULL,
    consent_revision INTEGER NOT NULL CHECK (consent_revision >= 1),
    disabled_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subscription_id, subject)
);
CREATE TABLE IF NOT EXISTS email_outbox (
    client_delivery_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    source_id TEXT NOT NULL CHECK (length(source_id) = 64),
    job_id TEXT,
    subscription_id TEXT REFERENCES subscriptions(id) ON DELETE SET NULL,
    consent_kind TEXT NOT NULL CHECK (
        consent_kind IN ('subscription_completion', 'manual')
    ),
    consent_revision INTEGER NOT NULL CHECK (consent_revision >= 1),
    manual_action_id TEXT,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'claimed', 'delivered', 'failed', 'cancelled')
    ),
    attempts INTEGER NOT NULL CHECK (attempts BETWEEN 0 AND 8),
    next_attempt_at TEXT,
    error_code TEXT CHECK (
        error_code IS NULL OR error_code IN (
            'premium_feature_unavailable', 'delivery_too_large',
            'idempotency_conflict', 'delivery_unavailable', 'email_not_verified'
        )
    ),
    claim_generation INTEGER NOT NULL CHECK (claim_generation >= 0),
    claimed_at TEXT,
    claim_expires_at TEXT,
    server_delivery_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    CHECK (
        (consent_kind = 'subscription_completion'
            AND job_id IS NOT NULL AND manual_action_id IS NULL) OR
        (consent_kind = 'manual' AND manual_action_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS email_outbox_automatic_unique
    ON email_outbox(subscription_id, job_id, consent_revision)
    WHERE consent_kind = 'subscription_completion';
CREATE UNIQUE INDEX IF NOT EXISTS email_outbox_manual_unique
    ON email_outbox(subject, manual_action_id)
    WHERE consent_kind = 'manual';
CREATE INDEX IF NOT EXISTS email_outbox_claim_idx
    ON email_outbox(subject, state, next_attempt_at, claim_expires_at, created_at);
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


def _row_to_preference(row: sqlite3.Row) -> EmailPreferenceRecord:
    return EmailPreferenceRecord(
        subscription_id=row["subscription_id"],
        subject=row["subject"],
        enabled_at=row["enabled_at"],
        consent_revision=row["consent_revision"],
        disabled_at=row["disabled_at"],
        updated_at=row["updated_at"],
    )


def _row_to_outbox(row: sqlite3.Row) -> EmailOutboxRecord:
    return EmailOutboxRecord(
        client_delivery_id=row["client_delivery_id"],
        subject=row["subject"],
        source_id=row["source_id"],
        job_id=row["job_id"],
        subscription_id=row["subscription_id"],
        consent_kind=row["consent_kind"],
        consent_revision=row["consent_revision"],
        manual_action_id=row["manual_action_id"],
        state=row["state"],
        attempts=row["attempts"],
        next_attempt_at=row["next_attempt_at"],
        error_code=row["error_code"],
        claim_generation=row["claim_generation"],
        claimed_at=row["claimed_at"],
        claim_expires_at=row["claim_expires_at"],
        server_delivery_id=row["server_delivery_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        delivered_at=row["delivered_at"],
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
            else:
                if version == 1:
                    rows = connection.execute(
                        "SELECT subscription_id, episode_key, published_at FROM episodes"
                    ).fetchall()
                    for row in rows:
                        normalized = _normalized_published_at(row["published_at"])
                        if normalized != row["published_at"]:
                            connection.execute(
                                """
                                UPDATE episodes SET published_at = ?
                                WHERE subscription_id = ? AND episode_key = ?
                                """,
                                (normalized, row["subscription_id"], row["episode_key"]),
                            )
                    version = 2
                if version == 2:
                    connection.executescript(_EMAIL_SCHEMA)
                    version = 3
                if version != SCHEMA_VERSION:
                    raise RuntimeError(f"unsupported subscription database schema {version}")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            connection.execute(
                """
                UPDATE email_outbox SET state = 'cancelled', next_attempt_at = NULL,
                    error_code = NULL, updated_at = ?
                WHERE subscription_id = ? AND state = 'pending'
                """,
                (now, subscription_id),
            )
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

    def discovered_episodes(self, subscription_id: str, *, limit: int) -> list[EpisodeRecord]:
        """Oldest discovered episodes eligible for the bounded job handoff."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM episodes
                WHERE subscription_id = ? AND state = 'discovered'
                ORDER BY
                    CASE WHEN published_at IS NULL THEN 1 ELSE 0 END,
                    published_at, created_at, episode_key
                LIMIT ?
                """,
                (subscription_id, limit),
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def episodes_for_reconciliation(self) -> list[EpisodeRecord]:
        """Rows whose discovery-to-job crash window or terminal state needs repair."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM episodes
                WHERE state IN ('discovered', 'queued')
                ORDER BY created_at, subscription_id, episode_key
                """
            ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def mark_episode_queued(
        self,
        subscription_id: str,
        episode_key: str,
        *,
        job_id: str,
        updated_at: str,
    ) -> None:
        """Atomically attach the idempotently submitted job to a discovery."""
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE episodes SET state = 'queued', job_id = ?, updated_at = ?
                WHERE subscription_id = ? AND episode_key = ? AND state = 'discovered'
                """,
                (job_id, updated_at, subscription_id, episode_key),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    """
                    SELECT state, job_id FROM episodes
                    WHERE subscription_id = ? AND episode_key = ?
                    """,
                    (subscription_id, episode_key),
                ).fetchone()
                if row is None:
                    raise KeyError((subscription_id, episode_key))
                if row["state"] != "queued" or row["job_id"] != job_id:
                    raise RuntimeError("episode cannot be attached to this job")

    def mark_episode_terminal(
        self,
        subscription_id: str,
        episode_key: str,
        *,
        state: str,
        updated_at: str,
        email_subject: str | None = None,
        client_delivery_id: str | None = None,
        source_id: str | None = None,
    ) -> str | None:
        """Record a reconciled job outcome without deleting local episode data."""
        if state not in {"completed", "failed"}:
            raise ValueError("invalid terminal episode state")
        if email_subject is not None and (client_delivery_id is None or source_id is None):
            raise ValueError("email completion requires delivery and source identities")
        inserted_delivery_id: str | None = None
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE episodes SET state = ?, updated_at = ?
                WHERE subscription_id = ? AND episode_key = ? AND state = 'queued'
                """,
                (state, updated_at, subscription_id, episode_key),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    """
                    SELECT state FROM episodes
                    WHERE subscription_id = ? AND episode_key = ?
                    """,
                    (subscription_id, episode_key),
                ).fetchone()
                if row is None:
                    raise KeyError((subscription_id, episode_key))
                if row["state"] != state:
                    raise RuntimeError("episode is not queued for reconciliation")
            elif state == "completed" and email_subject is not None:
                episode = connection.execute(
                    """
                    SELECT job_id FROM episodes
                    WHERE subscription_id = ? AND episode_key = ?
                    """,
                    (subscription_id, episode_key),
                ).fetchone()
                preference = connection.execute(
                    """
                    SELECT consent_revision FROM subscription_email_preferences
                    WHERE subscription_id = ? AND subject = ? AND disabled_at IS NULL
                      AND enabled_at <= ?
                    """,
                    (subscription_id, email_subject, updated_at),
                ).fetchone()
                if episode is not None and episode["job_id"] is not None and preference is not None:
                    inserted = connection.execute(
                        """
                        INSERT OR IGNORE INTO email_outbox (
                            client_delivery_id, subject, source_id, job_id, subscription_id,
                            consent_kind, consent_revision, manual_action_id, state, attempts,
                            next_attempt_at, error_code, claim_generation, claimed_at,
                            claim_expires_at, server_delivery_id, created_at, updated_at,
                            delivered_at
                        ) VALUES (?, ?, ?, ?, ?, 'subscription_completion', ?, NULL,
                            'pending', 0, ?, NULL, 0, NULL, NULL, NULL, ?, ?, NULL)
                        """,
                        (
                            client_delivery_id,
                            email_subject,
                            source_id,
                            episode["job_id"],
                            subscription_id,
                            preference["consent_revision"],
                            updated_at,
                            updated_at,
                            updated_at,
                        ),
                    )
                    if inserted.rowcount == 1:
                        inserted_delivery_id = client_delivery_id
        return inserted_delivery_id

    def set_email_preference(
        self,
        subscription_id: str,
        subject: str,
        *,
        enabled: bool,
        updated_at: str,
    ) -> EmailPreferenceRecord | None:
        """Enable/revoke one subject's standing consent for one subscription."""
        with self._transaction() as connection:
            subscription = connection.execute(
                "SELECT id FROM subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()
            if subscription is None:
                raise KeyError(subscription_id)
            row = connection.execute(
                """
                SELECT * FROM subscription_email_preferences
                WHERE subscription_id = ? AND subject = ?
                """,
                (subscription_id, subject),
            ).fetchone()
            if enabled:
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO subscription_email_preferences (
                            subscription_id, subject, enabled_at, consent_revision,
                            disabled_at, updated_at
                        ) VALUES (?, ?, ?, 1, NULL, ?)
                        """,
                        (subscription_id, subject, updated_at, updated_at),
                    )
                elif row["disabled_at"] is not None:
                    connection.execute(
                        """
                        UPDATE subscription_email_preferences
                        SET enabled_at = ?, consent_revision = consent_revision + 1,
                            disabled_at = NULL, updated_at = ?
                        WHERE subscription_id = ? AND subject = ?
                        """,
                        (updated_at, updated_at, subscription_id, subject),
                    )
            elif row is not None and row["disabled_at"] is None:
                connection.execute(
                    """
                    UPDATE subscription_email_preferences
                    SET disabled_at = ?, updated_at = ?
                    WHERE subscription_id = ? AND subject = ?
                    """,
                    (updated_at, updated_at, subscription_id, subject),
                )
                connection.execute(
                    """
                    UPDATE email_outbox SET state = 'cancelled', next_attempt_at = NULL,
                        error_code = NULL, updated_at = ?
                    WHERE subscription_id = ? AND subject = ?
                      AND consent_kind = 'subscription_completion' AND state = 'pending'
                    """,
                    (updated_at, subscription_id, subject),
                )
            result = connection.execute(
                """
                SELECT * FROM subscription_email_preferences
                WHERE subscription_id = ? AND subject = ?
                """,
                (subscription_id, subject),
            ).fetchone()
        return _row_to_preference(result) if result is not None else None

    def get_email_preference(
        self, subscription_id: str, subject: str
    ) -> EmailPreferenceRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM subscription_email_preferences
                WHERE subscription_id = ? AND subject = ?
                """,
                (subscription_id, subject),
            ).fetchone()
        return _row_to_preference(row) if row is not None else None

    def insert_manual_email(
        self,
        *,
        client_delivery_id: str,
        subject: str,
        source_id: str,
        action_id: str,
        created_at: str,
    ) -> EmailOutboxRecord:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO email_outbox (
                    client_delivery_id, subject, source_id, job_id, subscription_id,
                    consent_kind, consent_revision, manual_action_id, state, attempts,
                    next_attempt_at, error_code, claim_generation, claimed_at,
                    claim_expires_at, server_delivery_id, created_at, updated_at,
                    delivered_at
                ) VALUES (?, ?, ?, NULL, NULL, 'manual', 1, ?, 'pending', 0, ?,
                    NULL, 0, NULL, NULL, NULL, ?, ?, NULL)
                """,
                (
                    client_delivery_id,
                    subject,
                    source_id,
                    action_id,
                    created_at,
                    created_at,
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM email_outbox WHERE subject = ? AND manual_action_id = ?",
                (subject, action_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("manual email action was not persisted")
        return _row_to_outbox(row)

    def list_email_outbox(self) -> list[EmailOutboxRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM email_outbox ORDER BY created_at, client_delivery_id"
            ).fetchall()
        return [_row_to_outbox(row) for row in rows]

    def claim_email_outbox(
        self,
        subject: str,
        *,
        claimed_at: str,
        claim_expires_at: str,
    ) -> EmailOutboxRecord | None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE email_outbox SET state = 'cancelled', next_attempt_at = NULL,
                    error_code = NULL, updated_at = ?
                WHERE subject = ? AND consent_kind = 'subscription_completion'
                  AND ((state = 'pending') OR
                       (state = 'claimed' AND claim_expires_at <= ?))
                  AND NOT EXISTS (
                      SELECT 1 FROM subscription_email_preferences preference
                      WHERE preference.subscription_id = email_outbox.subscription_id
                        AND preference.subject = email_outbox.subject
                        AND preference.consent_revision = email_outbox.consent_revision
                        AND preference.disabled_at IS NULL
                  )
                """,
                (claimed_at, subject, claimed_at),
            )
            connection.execute(
                """
                UPDATE email_outbox SET state = 'failed', error_code = 'delivery_unavailable',
                    next_attempt_at = NULL, claimed_at = NULL, claim_expires_at = NULL,
                    updated_at = ?
                WHERE subject = ? AND attempts >= 8
                  AND (state = 'pending' OR (state = 'claimed' AND claim_expires_at <= ?))
                """,
                (claimed_at, subject, claimed_at),
            )
            row = connection.execute(
                """
                SELECT * FROM email_outbox
                WHERE subject = ? AND attempts < 8 AND (
                    (state = 'pending' AND next_attempt_at <= ?) OR
                    (state = 'claimed' AND claim_expires_at <= ?)
                )
                ORDER BY created_at, client_delivery_id LIMIT 1
                """,
                (subject, claimed_at, claimed_at),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE email_outbox SET state = 'claimed', attempts = attempts + 1,
                    claim_generation = claim_generation + 1, claimed_at = ?,
                    claim_expires_at = ?, next_attempt_at = NULL, error_code = NULL,
                    updated_at = ?
                WHERE client_delivery_id = ?
                """,
                (claimed_at, claim_expires_at, claimed_at, row["client_delivery_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                (row["client_delivery_id"],),
            ).fetchone()
        return _row_to_outbox(claimed)

    def complete_email_outbox(
        self,
        client_delivery_id: str,
        *,
        claim_generation: int,
        server_delivery_id: str,
        delivered_at: str,
        updated_at: str,
    ) -> EmailOutboxRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                (client_delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_delivery_id)
            if row["state"] == "delivered":
                if row["server_delivery_id"] != server_delivery_id:
                    raise RuntimeError("delivery completion conflicts with prior result")
                return _row_to_outbox(row)
            if row["state"] != "claimed" or row["claim_generation"] != claim_generation:
                raise RuntimeError("email claim is no longer current")
            connection.execute(
                """
                UPDATE email_outbox SET state = 'delivered', error_code = NULL,
                    next_attempt_at = NULL, claimed_at = NULL, claim_expires_at = NULL,
                    server_delivery_id = ?, delivered_at = ?, updated_at = ?
                WHERE client_delivery_id = ?
                """,
                (server_delivery_id, delivered_at, updated_at, client_delivery_id),
            )
            completed = connection.execute(
                "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                (client_delivery_id,),
            ).fetchone()
        return _row_to_outbox(completed)

    def release_email_outbox(
        self,
        client_delivery_id: str,
        *,
        claim_generation: int,
        error_code: str,
        next_attempt_at: str,
        updated_at: str,
    ) -> EmailOutboxRecord:
        terminal_errors = {"delivery_too_large", "idempotency_conflict", "email_not_verified"}
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                (client_delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_delivery_id)
            if row["state"] != "claimed" or row["claim_generation"] != claim_generation:
                raise RuntimeError("email claim is no longer current")
            preference_active = True
            if row["consent_kind"] == "subscription_completion":
                preference_active = (
                    connection.execute(
                        """
                        SELECT 1 FROM subscription_email_preferences
                        WHERE subscription_id = ? AND subject = ?
                          AND consent_revision = ? AND disabled_at IS NULL
                        """,
                        (row["subscription_id"], row["subject"], row["consent_revision"]),
                    ).fetchone()
                    is not None
                )
            if not preference_active:
                state = "cancelled"
                attempts = row["attempts"]
                stored_error = None
                retry_at = None
            elif error_code == "premium_feature_unavailable":
                state = "pending"
                attempts = max(0, int(row["attempts"]) - 1)
                stored_error = error_code
                retry_at = updated_at
            elif error_code in terminal_errors or row["attempts"] >= 8:
                state = "failed"
                attempts = row["attempts"]
                stored_error = error_code
                retry_at = None
            else:
                state = "pending"
                attempts = row["attempts"]
                stored_error = error_code
                retry_at = next_attempt_at
            connection.execute(
                """
                UPDATE email_outbox SET state = ?, attempts = ?, error_code = ?,
                    next_attempt_at = ?, claimed_at = NULL, claim_expires_at = NULL,
                    updated_at = ? WHERE client_delivery_id = ?
                """,
                (state, attempts, stored_error, retry_at, updated_at, client_delivery_id),
            )
            released = connection.execute(
                "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                (client_delivery_id,),
            ).fetchone()
        return _row_to_outbox(released)

    def cancel_email_outbox(self, client_delivery_id: str, *, updated_at: str) -> EmailOutboxRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                (client_delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(client_delivery_id)
            if row["state"] == "claimed":
                raise RuntimeError("email upload may already be in progress")
            if row["state"] == "pending":
                connection.execute(
                    """
                    UPDATE email_outbox SET state = 'cancelled', next_attempt_at = NULL,
                        error_code = NULL, updated_at = ? WHERE client_delivery_id = ?
                    """,
                    (updated_at, client_delivery_id),
                )
                row = connection.execute(
                    "SELECT * FROM email_outbox WHERE client_delivery_id = ?",
                    (client_delivery_id,),
                ).fetchone()
        return _row_to_outbox(row)

    @staticmethod
    def _episode_from_row(row: sqlite3.Row) -> EpisodeRecord:
        return EpisodeRecord(
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
            for table in (
                "subscriptions",
                "episodes",
                "subscription_email_preferences",
                "email_outbox",
            )
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
        published_at=_normalized_published_at(published_at),
        state="discovered",
        job_id=None,
        created_at=now,
        updated_at=now,
    )


def _normalized_published_at(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
