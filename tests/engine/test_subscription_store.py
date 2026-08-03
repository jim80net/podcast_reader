from __future__ import annotations

import os
import sqlite3
import stat
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.subscription_store import SCHEMA_VERSION, SubscriptionStore

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
        assert store.raw_connection_for_tests().execute("PRAGMA user_version").fetchone()[0] == 1
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
