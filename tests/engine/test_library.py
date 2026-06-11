"""Tests for podcast_reader.engine.library."""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING

from podcast_reader.engine.library import (
    add_entry,
    entry_dir,
    get_entry,
    list_entries,
    source_identity,
    stage_and_commit,
    validate_artifact,
)
from podcast_reader.types import LibraryEntry

if TYPE_CHECKING:
    from pathlib import Path


def _entry(source_id: str, *, source: str = "https://example.com/a") -> LibraryEntry:
    return LibraryEntry(
        source_id=source_id,
        source=source,
        title="Title",
        html_path=f"/lib/{source_id}/a.html",
        created_at=time.time(),
    )


class TestSourceIdentity:
    def test_url_identity_is_sha256_of_url(self) -> None:
        url = "https://example.com/episode.mp3"
        assert source_identity(url) == hashlib.sha256(url.encode()).hexdigest()

    def test_local_file_identity_is_sha256_of_bytes(self, tmp_path: Path) -> None:
        audio = tmp_path / "episode.mp3"
        audio.write_bytes(b"audio bytes")
        assert source_identity(str(audio)) == hashlib.sha256(b"audio bytes").hexdigest()

    def test_large_local_file_chunked_hash_matches_full_digest(self, tmp_path: Path) -> None:
        """Files larger than one hash chunk (1 MiB) produce the same identity
        as a whole-content sha256."""
        payload = bytes(range(256)) * (10 * 1024)  # 2.5 MiB, > 2 chunks
        audio = tmp_path / "big.mp3"
        audio.write_bytes(payload)
        assert source_identity(str(audio)) == hashlib.sha256(payload).hexdigest()

    def test_same_named_local_files_do_not_collide(self, tmp_path: Path) -> None:
        a_dir = tmp_path / "a"
        b_dir = tmp_path / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        (a_dir / "episode.mp3").write_bytes(b"first show")
        (b_dir / "episode.mp3").write_bytes(b"second show")
        id_a = source_identity(str(a_dir / "episode.mp3"))
        id_b = source_identity(str(b_dir / "episode.mp3"))
        assert id_a != id_b
        assert entry_dir(tmp_path, id_a) != entry_dir(tmp_path, id_b)


class TestEntryDir:
    def test_entry_dir_uses_full_id(self, tmp_path: Path) -> None:
        """The full 64-hex source_id avoids prefix collisions (C6)."""
        source_id = "abcdef0123456789" * 4
        assert entry_dir(tmp_path, source_id) == tmp_path / source_id


class TestIndex:
    def test_add_and_list_roundtrip(self, tmp_path: Path) -> None:
        entry = _entry("a" * 64)
        add_entry(tmp_path, entry)
        assert list_entries(tmp_path) == [entry]

    def test_same_source_id_replaces_entry(self, tmp_path: Path) -> None:
        first = _entry("a" * 64)
        second = _entry("a" * 64)
        second["title"] = "Updated"
        add_entry(tmp_path, first)
        add_entry(tmp_path, second)
        entries = list_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["title"] == "Updated"

    def test_get_entry(self, tmp_path: Path) -> None:
        entry = _entry("a" * 64)
        add_entry(tmp_path, entry)
        assert get_entry(tmp_path, "a" * 64) == entry
        assert get_entry(tmp_path, "b" * 64) is None

    def test_list_entries_empty_without_index(self, tmp_path: Path) -> None:
        assert list_entries(tmp_path) == []

    def test_index_survives_leftover_corrupt_tmp(self, tmp_path: Path) -> None:
        """A crash mid-write leaves a temp file; the committed index stays readable."""
        entry = _entry("a" * 64)
        add_entry(tmp_path, entry)
        # simulate a crash during a later write: torn temp file next to the index
        (tmp_path / "library.json.tmp").write_text('{"torn":')
        assert list_entries(tmp_path) == [entry]
        # and the next write still succeeds
        add_entry(tmp_path, _entry("b" * 64))
        assert len(list_entries(tmp_path)) == 2

    def test_index_write_is_atomic(self, tmp_path: Path) -> None:
        add_entry(tmp_path, _entry("a" * 64))
        assert json.loads((tmp_path / "library.json").read_text())
        assert not (tmp_path / "library.json.tmp").exists()


class TestValidateArtifact:
    def test_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text('{"segments": []}')
        assert validate_artifact(path) is True

    def test_corrupt_json_discarded(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text('{"segments": [')
        assert validate_artifact(path) is False
        assert not path.exists()

    def test_empty_html_discarded(self, tmp_path: Path) -> None:
        path = tmp_path / "a.html"
        path.touch()
        assert validate_artifact(path) is False
        assert not path.exists()

    def test_nonempty_html_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "a.html"
        path.write_text("<html></html>")
        assert validate_artifact(path) is True

    def test_missing_is_invalid(self, tmp_path: Path) -> None:
        assert validate_artifact(tmp_path / "nope.json") is False


class TestStageAndCommit:
    def test_final_absent_until_commit(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        staged = staging / "a.html"
        staged.write_text("<html>done</html>")
        final = tmp_path / "a.html"
        assert not final.exists()  # nothing in the entry until commit
        stage_and_commit(staged, final)
        assert final.read_text() == "<html>done</html>"

    def test_commit_preserves_staging_copy(self, tmp_path: Path) -> None:
        """Staging keeps its copy so re-submissions hit the pipeline cache."""
        staging = tmp_path / "staging"
        staging.mkdir()
        staged = staging / "a.json"
        staged.write_text('{"segments": []}')
        stage_and_commit(staged, tmp_path / "a.json")
        assert staged.exists()
        assert (tmp_path / "a.json").read_text() == '{"segments": []}'

    def test_commit_leaves_no_temp_file(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        staged = staging / "a.html"
        staged.write_text("<html></html>")
        stage_and_commit(staged, tmp_path / "a.html")
        assert list(tmp_path.glob("*.tmp")) == []

    def test_commit_overwrites_previous_artifact(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        (tmp_path / "a.html").write_text("<html>old</html>")
        staged = staging / "a.html"
        staged.write_text("<html>new</html>")
        stage_and_commit(staged, tmp_path / "a.html")
        assert (tmp_path / "a.html").read_text() == "<html>new</html>"
