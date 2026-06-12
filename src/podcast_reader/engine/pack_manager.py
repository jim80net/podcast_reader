"""Pack downloads and installation: staging, Range resume, sha256 verify.

Downloads stream to identity-bound staging partials (per S2): each ``.part``
file is named by the expected sha256 of the file it stages, so at install
start any partial whose identity does not match the current registry pin is
silently discarded — a pin bump restarts that file cleanly from zero instead
of resuming stale bytes into a doomed verification.

Every completed file is verified against the registry pin before it can be
installed; a mismatch deletes the partial and fails closed.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from podcast_reader.engine.packs import PackFilePin

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK_SIZE = 64 * 1024
PARTIAL_SUFFIX = ".part"

#: Generous read timeout: pack hosts stream multi-hundred-MB files; the
#: connect timeout stays tight so a dead host fails fast.
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)


class PackDownloadError(Exception):
    """A download failed structurally (HTTP error or sha256 mismatch).

    The message is self-authored, so the API layer may surface it verbatim.
    """


class InstallAbortedError(Exception):
    """The installer is shutting down; the partial stays on disk (resumable)."""


def _never_stop() -> bool:
    return False


def _no_progress(done: int) -> None:
    return None


def partial_path(staging: Path, pin: PackFilePin) -> Path:
    """The identity-bound staging partial for *pin* (named by sha256, per S2)."""
    return staging / f"{pin['sha256']}{PARTIAL_SUFFIX}"


def discard_stale_partials(staging: Path, expected_sha256s: Iterable[str]) -> None:
    """Delete staging partials whose identity is outside the current pins (per S2)."""
    expected = set(expected_sha256s)
    for part in staging.glob(f"*{PARTIAL_SUFFIX}"):
        if part.name.removesuffix(PARTIAL_SUFFIX) not in expected:
            logger.info("Discarding stale pack partial %s (pin moved)", part.name)
            part.unlink(missing_ok=True)


def download_file(
    client: httpx.Client,
    pin: PackFilePin,
    staging: Path,
    *,
    on_progress: Callable[[int], None] = _no_progress,
    should_stop: Callable[[], bool] = _never_stop,
) -> Path:
    """Download *pin* into its staging partial, resuming and verifying.

    Resumes via HTTP Range from the partial's byte offset; a server answering
    200 despite a Range header ignored it, so that file restarts from zero.
    *on_progress* receives the absolute byte count downloaded so far for this
    file. *should_stop* is polled between chunks; a stop raises
    :class:`InstallAbortedError`, leaving the partial on disk for resume.

    Returns the verified partial path; raises :class:`PackDownloadError` on
    HTTP failure or sha256 mismatch (the partial is deleted on mismatch —
    fail closed, per the checksum-verification requirement).
    """
    part = partial_path(staging, pin)
    offset = part.stat().st_size if part.exists() else 0
    if offset < pin["size"]:
        _stream_to_partial(client, pin, part, offset, on_progress, should_stop)
    _verify_sha256(part, pin)
    return part


def _stream_to_partial(
    client: httpx.Client,
    pin: PackFilePin,
    part: Path,
    offset: int,
    on_progress: Callable[[int], None],
    should_stop: Callable[[], bool],
) -> None:
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.stream("GET", pin["url"], headers=headers, follow_redirects=True) as response:
        if response.status_code == 206 and offset:
            mode = "ab"
        elif response.status_code == 200:
            mode = "wb"  # full body: fresh download, or the server ignored Range
            offset = 0
        else:
            raise PackDownloadError(
                f"download of {pin['path']} failed: HTTP {response.status_code}"
            )
        done = offset
        with part.open(mode) as fh:
            for chunk in response.iter_bytes(DOWNLOAD_CHUNK_SIZE):
                if should_stop():
                    raise InstallAbortedError(pin["path"])
                fh.write(chunk)
                done += len(chunk)
                on_progress(done)


def _verify_sha256(part: Path, pin: PackFilePin) -> None:
    digest = hashlib.sha256()
    with part.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != pin["sha256"]:
        part.unlink(missing_ok=True)
        raise PackDownloadError(
            f"sha256 mismatch for {pin['path']}: expected {pin['sha256']}, got {actual}"
        )
