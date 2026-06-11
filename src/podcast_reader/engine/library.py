"""Managed transcript library: source identity, atomic index, staged artifact writes.

The engine is the sole writer of the library index (``library.json`` inside the
library directory). Every index write is atomic (temp file + ``os.replace``).
Artifacts are produced in a per-entry staging directory and committed into the
entry directory atomically, so a crash mid-write never leaves a torn artifact
in the entry.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

from podcast_reader.engine.settings import atomic_write_json
from podcast_reader.pipeline import _valid_artifact

if TYPE_CHECKING:
    from podcast_reader.types import LibraryEntry

INDEX_FILE = "library.json"
STAGING_DIR_NAME = "staging"

_INDEX_LOCK = threading.Lock()


_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def source_identity(source: str) -> str:
    """Stable identity for a source: sha256 of the URL, or of file bytes locally.

    Keying local files by content means two different files that happen to share
    a name (``episode.mp3``) can never collide in the library. Local files are
    hashed in 1 MiB chunks so large media never loads into memory whole.
    """
    if source.startswith(("http://", "https://")):
        return hashlib.sha256(source.encode()).hexdigest()
    digest = hashlib.sha256()
    with Path(source).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entry_dir(library_dir: Path, source_id: str) -> Path:
    """Directory holding all committed artifacts for one source.

    Named by the full 64-hex source identity: a truncated prefix (48 bits)
    carries an avoidable collision risk, and the full hash keeps the path
    well within OS limits for ``~/PodcastReader/<id>/``.
    """
    return library_dir / source_id


def staging_dir(library_dir: Path, source_id: str) -> Path:
    """Per-entry staging directory where pipeline steps write their output.

    Persistent across runs: it doubles as the artifact cache for re-submissions
    (the pipeline's cache checks run against this directory).
    """
    return entry_dir(library_dir, source_id) / STAGING_DIR_NAME


def load_index(library_dir: Path) -> list[LibraryEntry]:
    """Read the library index, or an empty list when none exists yet."""
    path = library_dir / INDEX_FILE
    if not path.exists():
        return []
    return cast("list[LibraryEntry]", json.loads(path.read_text()))


def save_index(library_dir: Path, entries: list[LibraryEntry]) -> None:
    """Write the library index atomically (temp file + ``os.replace``)."""
    library_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(library_dir / INDEX_FILE, entries)


def add_entry(library_dir: Path, entry: LibraryEntry) -> None:
    """Insert or replace the index entry for ``entry["source_id"]``."""
    with _INDEX_LOCK:
        entries = [e for e in load_index(library_dir) if e["source_id"] != entry["source_id"]]
        entries.append(entry)
        save_index(library_dir, entries)


def list_entries(library_dir: Path) -> list[LibraryEntry]:
    """All library entries (insertion order)."""
    return load_index(library_dir)


def get_entry(library_dir: Path, source_id: str) -> LibraryEntry | None:
    """Look up one entry by full source identity."""
    for entry in load_index(library_dir):
        if entry["source_id"] == source_id:
            return entry
    return None


def validate_artifact(path: Path) -> bool:
    """True when a cached artifact is usable (JSON parses / HTML non-empty).

    Invalid artifacts are unlinked so the producing step re-runs. Delegates to
    the pipeline's shared check so CLI and engine agree on validity.
    """
    return _valid_artifact(path)


def stage_and_commit(staging_file: Path, final_path: Path) -> None:
    """Atomically publish a staged artifact into the entry directory.

    Copies to a temp file beside the final path, then ``os.replace``-swaps it
    in, so the entry never holds a torn artifact. The staging copy is kept:
    it is the pipeline's cache for re-submissions of the same source.
    """
    tmp = final_path.with_suffix(final_path.suffix + ".tmp")
    shutil.copy2(staging_file, tmp)
    tmp.replace(final_path)
