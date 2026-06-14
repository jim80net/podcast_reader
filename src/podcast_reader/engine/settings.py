"""Engine settings and engine-owned state persistence.

Two separate files live under the engine data dir (default ``~/PodcastReader``,
override via ``PODCAST_READER_DATA_DIR``):

- ``engine-state.json`` (mode 0600) — engine-owned ``{port, token}``; never
  exposed for writing via the API.
- ``settings.json`` — user settings, readable and writable via
  ``GET/PUT /v1/settings``.

All writes are atomic (temp file + ``os.replace``) under a module-level lock.

Note on Windows: POSIX mode bits like 0600 are effectively a no-op there
(``os.chmod``/``os.open`` modes only toggle the read-only flag); secrets such
as the engine token are instead protected by the user-profile directory ACLs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import stat
import threading
from importlib import metadata
from pathlib import Path
from typing import TypedDict, cast

from podcast_reader.types import EngineSettings

logger = logging.getLogger(__name__)

ENGINE_STATE_FILE = "engine-state.json"
SETTINGS_FILE = "settings.json"

#: The chapter_model literal Phase 1 persisted explicitly (Anthropic was the
#: only provider then). Kept as a historical constant — it must keep matching
#: old files even if the anthropic registry default changes later.
_PHASE1_CHAPTER_MODEL = "claude-haiku-4-5-20251001"

_WRITE_LOCK = threading.Lock()


class EngineState(TypedDict):
    """Engine-owned state: the bound port and the API bearer token."""

    port: int
    token: str


def data_dir_path() -> Path:
    """Resolve the engine data directory path without creating it.

    For read-only consumers (the whisper worker's DLL-path prep, ytdlp's
    managed-copy residence gate) that must not leave a ``~/PodcastReader``
    behind as a side effect. Default is ``~/PodcastReader``;
    ``PODCAST_READER_DATA_DIR`` overrides.
    """
    env = os.environ.get("PODCAST_READER_DATA_DIR")
    return Path(env).expanduser() if env else Path.home() / "PodcastReader"


def data_dir() -> Path:
    """Resolve (and create) the engine data directory."""
    base = data_dir_path()
    base.mkdir(parents=True, exist_ok=True)
    return base


def load_engine_state(base: Path) -> EngineState:
    """Load engine state, creating it (port 0, fresh token, mode 0600) on first run.

    A pre-existing file with permissive mode bits is re-hardened to 0600 on
    load (POSIX only): the file holds the bearer token, so a one-time loose
    copy (restored backup, manual edit) must not stay world-readable forever.
    """
    path = base / ENGINE_STATE_FILE
    if path.exists():
        _ensure_owner_only(path)
        return cast("EngineState", json.loads(path.read_text()))
    state = EngineState(port=0, token=secrets.token_urlsafe(32))
    save_engine_state(base, state)
    return state


def save_engine_state(base: Path, state: EngineState) -> None:
    """Persist engine state atomically with owner-only permissions."""
    atomic_write_json(base / ENGINE_STATE_FILE, state, mode=0o600)


def load_settings(base: Path) -> EngineSettings:
    """Load user settings, falling back to defaults without persisting them.

    Loaded values are merged over :func:`default_settings`, so a settings file
    persisted by an earlier version (lacking newer fields) upgrades cleanly —
    no job may fail because the file predates a release.

    A Phase 1 file (no ``chapter_provider``) carrying exactly the Phase 1
    default ``chapter_model`` has its model normalized to ``""`` ("provider
    default") during the merge: that value was installed by us, not chosen by
    the user, and a later provider switch must not send an Anthropic model id
    to another provider. Any other persisted model is a deliberate user
    choice and is preserved verbatim.

    A malformed settings file is quarantined to ``settings.json.corrupt``
    (with a warning, mirroring the job-journal handling) and defaults are
    returned — bad settings must never prevent the engine from serving.
    """
    path = base / SETTINGS_FILE
    if not path.exists():
        return default_settings(base)
    try:
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise TypeError("settings must be a JSON object")
    except (OSError, ValueError, TypeError) as exc:
        _quarantine(path, exc)
        return default_settings(base)
    if "chapter_provider" not in loaded and loaded.get("chapter_model") == _PHASE1_CHAPTER_MODEL:
        loaded = {**loaded, "chapter_model": ""}
    return cast("EngineSettings", {**default_settings(base), **loaded})


def save_settings(base: Path, settings: EngineSettings) -> None:
    """Persist user settings atomically."""
    atomic_write_json(base / SETTINGS_FILE, settings)


def default_settings(base: Path) -> EngineSettings:
    """Default settings, mirroring the CLI's environment-variable defaults."""
    return EngineSettings(
        whisper_model="large-v3",
        whisper_lang="en",
        whisper_device="cuda",
        sentences=5,
        library_dir=str(base / "library"),
        chapter_model="",  # "" means: the chapter provider's default model
        chapter_provider="anthropic",
        custom_provider_url="",
        diarize=False,
        media_cache_max_bytes=5 * 1024**3,  # 5 GiB LRU cap for the lazy media cache
    )


def token_fingerprint(token: str) -> str:
    """Non-reversible token identifier safe to publish in the discovery file."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def engine_version() -> str:
    """Installed package version, or a dev placeholder when not installed."""
    try:
        return metadata.version("podcast-reader")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


def atomic_write_json(path: Path, payload: object, *, mode: int | None = None) -> None:
    """Write *payload* as JSON via :func:`atomic_write_text`."""
    atomic_write_text(path, json.dumps(payload, indent=2), mode=mode)


def atomic_write_text(path: Path, data: str, *, mode: int | None = None) -> None:
    """Write *data* via temp file + ``os.replace`` under the module lock.

    With *mode*, the temp file is created with that mode applied at ``os.open``
    time (``O_CREAT | O_EXCL``), so the payload is never readable by other
    users, even transiently. Public so secret-bearing writers outside this
    module (the cookie-jar store) share one secure-write implementation.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _WRITE_LOCK:
        if mode is None:
            tmp.write_text(data)
        else:
            _secure_write_text(tmp, data, mode)
        os.replace(tmp, path)


def ensure_owner_only_dir(path: Path) -> None:
    """Re-harden an existing secret-bearing directory to 0700 (POSIX only).

    ``mkdir(mode=0o700, exist_ok=True)`` applies the mode only on creation —
    a directory that already existed with loose permissions keeps them. Public
    for the same reason as :func:`atomic_write_text`: secret-bearing writers
    outside this module (the cookie-jar store) share one hardening
    implementation. No-op on Windows (see module docstring — ACLs carry the
    protection there).
    """
    if os.name != "posix":  # pragma: no cover — exercised on Windows only
        return
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        path.chmod(0o700)


def _ensure_owner_only(path: Path) -> None:
    """Re-harden an existing secret file to 0600 (POSIX; no-op on Windows).

    On Windows POSIX mode bits only toggle the read-only flag (see module
    docstring); the user-profile directory ACLs protect the token there.
    """
    if os.name != "posix":  # pragma: no cover — exercised on Windows only
        return
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.chmod(0o600)


def _quarantine(path: Path, exc: Exception) -> None:
    """Move a corrupt file aside as ``<name>.corrupt``, logging either way."""
    corrupt = path.with_name(path.name + ".corrupt")
    try:
        path.replace(corrupt)
    except OSError as rename_exc:
        logger.warning(
            "%s unreadable (%s); quarantine rename failed (%s); using defaults",
            path.name,
            exc,
            rename_exc,
        )
        return
    logger.warning(
        "%s unreadable; quarantined to %s and using defaults: %s", path.name, corrupt, exc
    )


def _secure_write_text(path: Path, data: str, mode: int) -> None:
    """Create *path* carrying *mode* from the moment it exists, then write *data*."""
    path.unlink(missing_ok=True)  # a temp file left by a crash would trip O_EXCL
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
