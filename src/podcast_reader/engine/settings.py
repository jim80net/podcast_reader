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

_WRITE_LOCK = threading.Lock()


class EngineState(TypedDict):
    """Engine-owned state: the bound port and the API bearer token."""

    port: int
    token: str


def data_dir() -> Path:
    """Resolve (and create) the engine data directory.

    Default is ``~/PodcastReader``; ``PODCAST_READER_DATA_DIR`` overrides.
    """
    env = os.environ.get("PODCAST_READER_DATA_DIR")
    base = Path(env).expanduser() if env else Path.home() / "PodcastReader"
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
    """Write *payload* as JSON via temp file + ``os.replace`` under the module lock.

    With *mode*, the temp file is created with that mode applied at ``os.open``
    time (``O_CREAT | O_EXCL``), so the payload is never readable by other
    users, even transiently.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2)
    with _WRITE_LOCK:
        if mode is None:
            tmp.write_text(data)
        else:
            _secure_write_text(tmp, data, mode)
        os.replace(tmp, path)


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
