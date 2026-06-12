"""Managed external tools: bundle-seed reconciliation and yt-dlp self-update.

Design decision 7/8: the signed install dir is immutable, so the engine build
ships yt-dlp/ffmpeg/ffprobe *seeds* (plus a generated ``tools-manifest.json``
of ``{name: version}``) inside the bundle's ``tools/`` dir, and at every
engine startup they are reconciled into ``<data_dir>/tools/`` — copy when the
user-data copy is absent or the seed version is newer than the recorded
user-data version (newer-wins, the tool-resolution spec's seeding-time
contract). Versions are compared via manifests, never by executing binaries.
The engine then exports ``PODCAST_READER_TOOLS_DIR`` (when unset) so every
existing ``resolve_tool`` call site picks up the managed copies with zero
call-site changes.

yt-dlp self-updates (``yt-dlp -U``) run only against the user-data copy —
gated purely on the resolved binary *residing* in the user-data tools dir
(per Q3; release binaries support ``-U``, a PATH/pip copy does not) — on a
24 h schedule recorded in the user-data manifest. The failure-triggered
single-retry hook lives in ``ytdlp.py`` and reuses :func:`is_managed` /
:func:`run_ytdlp_self_update` / :func:`record_ytdlp_update` from here.

Failure posture throughout: log and continue — a broken seed or a failed
update check must never prevent the engine from serving.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import TypedDict

from podcast_reader.engine.settings import atomic_write_json, data_dir_path
from podcast_reader.tools import resolve_tool, run_child

logger = logging.getLogger(__name__)

TOOLS_MANIFEST = "tools-manifest.json"

#: Scheduled self-update cadence (design decision 8): at startup, when the
#: last successful check is older than this, run ``yt-dlp -U`` in background.
UPDATE_CHECK_INTERVAL_S = 24 * 60 * 60


class ToolsManifest(TypedDict):
    """User-data ``<data_dir>/tools/tools-manifest.json``.

    ``versions`` mirrors the bundle seed manifest's ``{name: version}``
    mapping (and tracks ``yt-dlp -U`` bumps); ``last_update_check`` is the
    epoch time of the last *successful* scheduled update check.
    """

    versions: dict[str, str]
    last_update_check: float


def tools_dir(base: Path) -> Path:
    """The managed tools directory under the engine data dir."""
    return base / "tools"


def bundle_tools_dir() -> Path | None:
    """The frozen bundle's seed ``tools/`` dir, or ``None`` when unfrozen."""
    if not getattr(sys, "frozen", False):
        return None
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return bundle / "tools"


def load_user_manifest(base: Path) -> ToolsManifest:
    """The user-data tools manifest; absent or corrupt reads as empty."""
    path = tools_dir(base) / TOOLS_MANIFEST
    try:
        loaded = json.loads(path.read_text())
        versions = loaded["versions"]
        last_check = loaded["last_update_check"]
        if not isinstance(versions, dict) or not isinstance(last_check, (int, float)):
            raise TypeError("malformed tools manifest")
    except (OSError, ValueError, TypeError, KeyError):
        return ToolsManifest(versions={}, last_update_check=0.0)
    return ToolsManifest(
        versions={str(name): str(version) for name, version in versions.items()},
        last_update_check=float(last_check),
    )


def save_user_manifest(base: Path, manifest: ToolsManifest) -> None:
    """Persist the user-data tools manifest atomically."""
    target = tools_dir(base)
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target / TOOLS_MANIFEST, manifest)


def seed_tools(base: Path, seed_dir: Path | None = None) -> None:
    """Reconcile bundle seeds into ``<data_dir>/tools/`` (newer wins).

    For each tool in the seed manifest: copy (atomic — temp + rename,
    ``copy2`` preserving execute bits) when the user-data copy is absent or
    the seed's version is newer than the recorded user-data version; a
    user-data copy whose recorded version is newer (a prior ``yt-dlp -U``)
    stays untouched. Per-tool failures log and continue; the engine serves
    regardless.
    """
    if seed_dir is None:
        seed_dir = bundle_tools_dir()
    if seed_dir is None:
        return
    seed_manifest = _load_seed_manifest(seed_dir)
    if seed_manifest is None:
        return
    target = tools_dir(base)
    user_manifest = load_user_manifest(base)
    changed = False
    for name, seed_version in seed_manifest.items():
        try:
            seed_file = _seed_file(seed_dir, name)
            if seed_file is None:
                logger.warning("Seed manifest lists %s but no seed binary exists; skipping", name)
                continue
            destination = target / seed_file.name
            recorded = user_manifest["versions"].get(name)
            if (
                destination.exists()
                and recorded is not None
                and not _is_newer(seed_version, recorded)
            ):
                continue
            target.mkdir(parents=True, exist_ok=True)
            tmp = target / (seed_file.name + ".tmp")
            shutil.copy2(seed_file, tmp)
            os.replace(tmp, destination)
            user_manifest["versions"][name] = seed_version
            changed = True
            logger.info("Seeded %s %s into %s", name, seed_version, target)
        except OSError as exc:
            logger.warning("Seeding %s failed; the engine serves anyway: %s", name, exc)
    if changed:
        try:
            save_user_manifest(base, user_manifest)
        except OSError as exc:
            logger.warning("Writing the tools manifest failed: %s", exc)


def export_tools_dir(base: Path) -> None:
    """Export ``PODCAST_READER_TOOLS_DIR`` for this process when unset.

    An explicitly set variable is respected (spec: explicit override wins).
    """
    os.environ.setdefault("PODCAST_READER_TOOLS_DIR", str(tools_dir(base)))


def is_managed(binary: str, base: Path | None = None) -> bool:
    """True when *binary* resides inside the user-data tools dir (the Q3 gate).

    Residence alone decides self-update eligibility: the managed copy is a
    release binary supporting ``-U``, while PATH- or env-resolved copies
    (dev environments, pip installs) must never be touched.
    """
    if base is None:
        base = data_dir_path()
    try:
        return Path(binary).resolve().is_relative_to(tools_dir(base).resolve())
    except OSError:
        return False


def run_ytdlp_self_update(binary: str) -> str | None:
    """Run ``yt-dlp -U`` on *binary*; return the post-update version, or ``None``.

    The version is re-read from ``yt-dlp --version`` after a successful
    update (the design's record-from-output rule — never trust assumptions
    about what ``-U`` installed). Any failure returns ``None``.
    """
    try:
        update = run_child([binary, "-U"])
    except OSError as exc:
        logger.warning("yt-dlp self-update could not run: %s", exc)
        return None
    if update.returncode != 0:
        logger.warning("yt-dlp self-update failed: %s", update.stderr.strip())
        return None
    try:
        version = run_child([binary, "--version"])
    except OSError as exc:
        logger.warning("yt-dlp --version after self-update could not run: %s", exc)
        return None
    if version.returncode != 0:
        logger.warning("yt-dlp --version after self-update failed: %s", version.stderr.strip())
        return None
    return version.stdout.strip()


def record_ytdlp_update(base: Path, version: str, checked_at: float) -> None:
    """Record a successful yt-dlp self-update in the user-data manifest.

    Keeping the recorded version current is what stops a later, older bundle
    seed from clobbering the self-updated copy (newer-wins comparison).
    """
    manifest = load_user_manifest(base)
    manifest["versions"]["yt-dlp"] = version
    manifest["last_update_check"] = checked_at
    save_user_manifest(base, manifest)


def maybe_self_update_ytdlp(base: Path, *, now: float | None = None) -> bool:
    """Scheduled self-update: run when the last recorded check is > 24 h old.

    Only when the resolved yt-dlp resides in the user-data tools dir; the
    check time is recorded on success only, so a failed attempt retries at
    the next startup. Designed to run on a background thread — never raises.
    Returns ``True`` when an update ran and was recorded.
    """
    try:
        current = time.time() if now is None else now
        manifest = load_user_manifest(base)
        if current - manifest["last_update_check"] < UPDATE_CHECK_INTERVAL_S:
            return False
        binary = resolve_tool("yt-dlp")
        if not is_managed(binary, base):
            return False
        version = run_ytdlp_self_update(binary)
        if version is None:
            return False
        record_ytdlp_update(base, version, current)
        return True
    except Exception:  # background thread — must never propagate
        logger.exception("Scheduled yt-dlp self-update failed")
        return False


def _load_seed_manifest(seed_dir: Path) -> dict[str, str] | None:
    """The bundle's flat ``{name: version}`` seed manifest, or ``None``."""
    path = seed_dir / TOOLS_MANIFEST
    try:
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise TypeError("seed manifest must be a JSON object")
    except (OSError, ValueError, TypeError) as exc:
        if path.exists():
            logger.warning("Unreadable seed manifest %s; skipping seeding: %s", path, exc)
        return None
    return {str(name): str(version) for name, version in loaded.items()}


def _seed_file(seed_dir: Path, name: str) -> Path | None:
    """The seed binary for *name* (``name`` or ``name.exe``), or ``None``."""
    for candidate in (seed_dir / name, seed_dir / f"{name}.exe"):
        if candidate.is_file():
            return candidate
    return None


def _is_newer(candidate: str, recorded: str) -> bool:
    """Numeric-segment version comparison (``2024.10.07`` > ``2024.9.1``)."""
    return _version_key(candidate) > _version_key(recorded)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))
