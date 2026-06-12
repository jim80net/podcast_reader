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

import contextlib
import hashlib
import logging
import os
import re
import shutil
import sys
import threading
import zipfile
from typing import TYPE_CHECKING, Any

import httpx

from podcast_reader.engine.events import EventBus
from podcast_reader.engine.hardware import detect_hardware, recommended_pack_ids
from podcast_reader.engine.jobs import WakeQueue
from podcast_reader.engine.packs import (
    PACK_SCHEMA,
    REGISTRY,
    ManifestFile,
    PackInstallError,
    PackManifest,
    PackProgress,
    PacksResponse,
    PackStatus,
    compat_error,
    files_error,
    is_published,
    manifest_path,
    pack_dir,
    pack_total_size,
    platform_supported,
    read_manifest,
)
from podcast_reader.engine.settings import atomic_write_json
from podcast_reader.types import PipelineEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from collections.abc import Set as AbstractSet
    from pathlib import Path

    from podcast_reader.engine.packs import HardwareInfo, PackEntry, PackFilePin, PackState

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK_SIZE = 64 * 1024
PARTIAL_SUFFIX = ".part"

#: Generous read timeout: pack hosts stream multi-hundred-MB files; the
#: connect timeout stays tight so a dead host fails fast.
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)


class PackDownloadError(Exception):
    """A download failed structurally (HTTP error or sha256 mismatch).

    The message is self-authored, so the API layer may surface it verbatim.
    *code* feeds the structured pack error (``download_failed`` for
    transport/HTTP problems, ``verification_failed`` for hash mismatches).
    """

    def __init__(self, message: str, *, code: str = "download_failed") -> None:
        super().__init__(message)
        self.code = code


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
            f"sha256 mismatch for {pin['path']}: expected {pin['sha256']}, got {actual}",
            code="verification_failed",
        )


# ---------------------------------------------------------------------------
# PackManager — dedicated installer thread, atomic install, manifest-first
# uninstall, startup validation
# ---------------------------------------------------------------------------


class UnknownPackError(Exception):
    """The pack id is not in the registry (HTTP 404)."""


class PackUnavailableError(Exception):
    """The pack is unpublished (per S5) or platform-gated (HTTP 409).

    The message is self-authored, safe to echo as response detail.
    """


class PackInstallingError(Exception):
    """The pack is currently installing, so the request conflicts (HTTP 409)."""


class PackManager:
    """Pack installs on a dedicated FIFO installer thread.

    Deliberately NOT the job-store worker (design decision 1): the job queue
    is single-worker FIFO, so a ~1 GB CUDA download would serialize against
    transcription jobs; download semantics (partial -> resume) also mismatch
    job semantics (interrupted -> retry). One transfer runs at a time, FIFO
    across packs; progress publishes into the shared :class:`EventBus` (the
    public seam, per S6) and ``GET /v1/packs`` is the hydration source of
    truth.

    Pack state needs no journal — it derives from disk plus in-memory
    installer state: installed manifest present -> installed; staging
    partials present -> resumable; else absent. Install is
    atomic-by-construction (files placed, manifest written last); uninstall
    inverts it (manifest deleted first, per S1).
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        bus: EventBus | None = None,
        registry: dict[str, PackEntry] | None = None,
        transport: httpx.BaseTransport | None = None,
        platform: str = sys.platform,
        progress_step: int = 4 * 1024 * 1024,
        hardware_provider: Callable[[], HardwareInfo] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._bus = bus if bus is not None else EventBus()
        self._registry = registry if registry is not None else REGISTRY
        self._transport = transport
        self._platform = platform
        self._progress_step = progress_step
        self._hardware_provider = (
            hardware_provider
            if hardware_provider is not None
            else lambda: detect_hardware(platform)
        )
        self._lock = threading.Lock()
        self._installing: dict[str, PackProgress] = {}
        self._errors: dict[str, PackInstallError] = {}
        self._queue = WakeQueue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    # -- public API ------------------------------------------------------

    def start_worker(self) -> None:
        """Start the installer thread (idempotent; restartable after shutdown)."""
        with self._lock:
            if self._worker is not None:
                return
            stop = threading.Event()
            self._stop = stop
            self._worker = threading.Thread(
                target=self._work_loop, args=(stop,), name="pack-installer", daemon=True
            )
            self._worker.start()

    def shutdown(self) -> None:
        """Stop the installer; an in-flight download aborts, staying resumable."""
        with self._lock:
            worker = self._worker
            self._worker = None
            stop = self._stop
        stop.set()
        self._queue.wake_all()
        if worker is None:
            return
        worker.join(timeout=30)

    def request_install(self, pack_id: str) -> None:
        """Enqueue an install (idempotent while installing or installed).

        Raises :class:`UnknownPackError` for ids outside the registry and
        :class:`PackUnavailableError` for unpublished (per S5) or
        platform-gated entries. Re-requesting a ``failed`` or
        ``incompatible`` pack re-downloads it (the re-download affordance,
        per S8).
        """
        entry = self._registry.get(pack_id)
        if entry is None:
            raise UnknownPackError(pack_id)
        if not is_published(entry):
            raise PackUnavailableError(
                f"pack {pack_id!r} has no published artifact yet and cannot be installed"
            )
        if not platform_supported(entry, self._platform):
            raise PackUnavailableError(
                f"pack {pack_id!r} is not available on platform {self._platform!r}"
            )
        with self._lock:
            if pack_id in self._installing:
                return  # idempotent: already installing (or queued)
            if self._installed_cleanly(entry):
                return  # idempotent: nothing to do
            self._errors.pop(pack_id, None)
            self._installing[pack_id] = PackProgress(bytes=0, total=pack_total_size(entry))
        self._queue.put(pack_id)

    def uninstall(self, pack_id: str) -> None:
        """Remove a pack: manifest first (atomic not-installed), then files.

        Per S1: deleting ``pack-manifest.json`` first means a job racing the
        uninstall observes a structured missing-pack failure at step start at
        worst — never a partial read — so a running job is no reason to
        refuse. 409 (:class:`PackInstallingError`) applies only while the
        pack itself is installing.
        """
        entry = self._registry.get(pack_id)
        if entry is None:
            raise UnknownPackError(pack_id)
        target = pack_dir(self._data_dir, entry)
        with self._lock:
            if pack_id in self._installing:
                raise PackInstallingError(
                    f"pack {pack_id!r} is currently installing; uninstall is refused"
                )
            manifest = read_manifest(target)
            manifest_path(target).unlink(missing_ok=True)  # FIRST (per S1)
            self._errors.pop(pack_id, None)
        if manifest is not None:
            self._remove_files(target, manifest)
        shutil.rmtree(self.staging_dir(pack_id), ignore_errors=True)
        self._publish_state(
            pack_id, "not-installed", message=f"{entry['display_name']} uninstalled"
        )

    def packs_response(self) -> PacksResponse:
        """Body of ``GET /v1/packs``: one round-trip gives the wizard
        hardware, recommendations, and per-pack state (design decision 9)."""
        hw = self._hardware_provider()
        return PacksResponse(hardware=hw, packs=self.statuses(recommended_pack_ids(hw)))

    def statuses(self, recommended: AbstractSet[str] = frozenset()) -> list[PackStatus]:
        """Per-pack status in registry order, derived from disk + memory."""
        return [
            self._status(entry, entry["id"] in recommended) for entry in self._registry.values()
        ]

    def validate_installed(self) -> dict[str, str]:
        """Startup validation pass: flag and log every unusable manifest.

        Same code as the live status derivation (validation at install time,
        startup, and listing must agree); returns pack id -> error message
        for everything flagged ``incompatible`` or ``failed`` (per S8).
        """
        flagged: dict[str, str] = {}
        for pack_id, entry in self._registry.items():
            target = pack_dir(self._data_dir, entry)
            manifest = read_manifest(target)
            if manifest is None:
                continue
            error = compat_error(entry, manifest) or files_error(target, manifest)
            if error is not None:
                flagged[pack_id] = error
                logger.warning("Installed pack %s is unusable: %s", pack_id, error)
        return flagged

    def staging_dir(self, pack_id: str) -> Path:
        """Identity-bound download staging for one pack (per S2)."""
        return self._data_dir / "pack-staging" / pack_id

    @property
    def bus(self) -> EventBus:
        """The shared publish seam pack events ride (per S6)."""
        return self._bus

    # -- status derivation -------------------------------------------------

    def _status(self, entry: PackEntry, recommended: bool) -> PackStatus:
        pack_id = entry["id"]
        status = PackStatus(
            id=pack_id,
            kind=entry["kind"],
            display_name=entry["display_name"],
            size=pack_total_size(entry),
            state="not-installed",
            recommended=recommended,
            installed_version=None,
            progress=None,
            error=None,
            licenses=list(entry["licenses"]),
        )
        if not is_published(entry) or not platform_supported(entry, self._platform):
            status["state"] = "unavailable"
            return status
        with self._lock:
            progress = self._installing.get(pack_id)
            error = self._errors.get(pack_id)
        if progress is not None:
            status["state"] = "installing"
            status["progress"] = progress
            return status
        target = pack_dir(self._data_dir, entry)
        manifest = read_manifest(target)
        if manifest is not None:
            # On-disk packs attribute what was actually installed (task 8.1):
            # the manifest's recorded notices, not the live registry's.
            status["licenses"] = list(manifest.get("licenses", []))
            incompat = compat_error(entry, manifest)
            if incompat is not None:
                status["state"] = "incompatible"
                status["error"] = PackInstallError(code="incompatible", message=incompat)
                return status
            integrity = files_error(target, manifest)
            if integrity is not None:
                status["state"] = "failed"
                status["error"] = PackInstallError(code="integrity", message=integrity)
                return status
            status["state"] = "installed"
            status["installed_version"] = manifest["version"]
            return status
        if error is not None:
            status["state"] = "failed"
            status["error"] = error
            return status
        if any(self.staging_dir(pack_id).glob(f"*{PARTIAL_SUFFIX}")):
            status["state"] = "resumable"
        return status

    def _installed_cleanly(self, entry: PackEntry) -> bool:
        """True when a valid, compatible, intact manifest is on disk."""
        target = pack_dir(self._data_dir, entry)
        manifest = read_manifest(target)
        if manifest is None:
            return False
        return compat_error(entry, manifest) is None and files_error(target, manifest) is None

    # -- installer worker --------------------------------------------------

    def _work_loop(self, stop: threading.Event) -> None:
        while True:
            pack_id = self._queue.get_or_stop(stop)
            if pack_id is None:
                return
            try:
                self._install(pack_id, stop)
            except InstallAbortedError:
                # Shutdown mid-download: partials stay resumable; quietly
                # drop the in-memory installing mark.
                with self._lock:
                    self._installing.pop(pack_id, None)
            except (PackDownloadError, httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
                self._fail(pack_id, exc)
            except Exception:  # the only installer must survive anything
                logger.exception("Pack install %s escaped _install; marking failed", pack_id)
                with contextlib.suppress(Exception):
                    self._fail(pack_id, PackDownloadError("internal installer error"))

    def _install(self, pack_id: str, stop: threading.Event) -> None:
        entry = self._registry[pack_id]
        files = entry["files"]
        assert files is not None  # guarded by request_install  # noqa: S101
        staging = self.staging_dir(pack_id)
        staging.mkdir(parents=True, exist_ok=True)
        discard_stale_partials(staging, {pin["sha256"] for pin in files})
        self._publish_state(pack_id, "installing", message=f"Installing {entry['display_name']}")
        total = pack_total_size(entry)
        done_by_sha = {
            pin["sha256"]: min(self._partial_size(staging, pin), pin["size"]) for pin in files
        }
        last_published = -self._progress_step  # so the first chunk publishes

        def progress_for(pin: PackFilePin) -> Callable[[int], None]:
            def on_progress(file_done: int) -> None:
                nonlocal last_published
                done_by_sha[pin["sha256"]] = file_done
                done = sum(done_by_sha.values())
                with self._lock:
                    if pack_id in self._installing:
                        self._installing[pack_id] = PackProgress(bytes=done, total=total)
                if done - last_published >= self._progress_step or done >= total:
                    last_published = done
                    self._publish_progress(pack_id, done, total)

            return on_progress

        with httpx.Client(transport=self._transport, timeout=DOWNLOAD_TIMEOUT) as client:
            parts = [
                download_file(
                    client,
                    pin,
                    staging,
                    on_progress=progress_for(pin),
                    should_stop=stop.is_set,
                )
                for pin in files
            ]

        target = pack_dir(self._data_dir, entry)
        target.mkdir(parents=True, exist_ok=True)
        # Reinstall over an existing pack: drop the OLD manifest before the
        # first file lands (mirroring uninstall's manifest-first discipline,
        # per S1) — a job validating at step start mid-reinstall reads
        # not-installed, never the old manifest over mixed old/new files.
        manifest_path(target).unlink(missing_ok=True)
        if entry["extract_wheels"]:
            manifest_files = self._extract_wheels(parts, target)
        else:
            manifest_files = self._place_files(files, parts, target)
        manifest = PackManifest(
            pack_schema=PACK_SCHEMA,
            id=pack_id,
            version=entry["version"],
            component_versions=dict(entry["component_versions"]),
            files=manifest_files,
            licenses=list(entry["licenses"]),
        )
        # Manifest written LAST: a pack dir without a valid manifest is by
        # definition not installed, so a crash anywhere above leaves no
        # phantom pack (atomic-by-construction).
        atomic_write_json(manifest_path(target), manifest)
        shutil.rmtree(staging, ignore_errors=True)
        with self._lock:
            self._installing.pop(pack_id, None)
        self._publish_state(pack_id, "installed", message=f"{entry['display_name']} installed")

    def _fail(self, pack_id: str, exc: Exception) -> None:
        code = getattr(exc, "code", "download_failed")
        error = PackInstallError(code=code, message=str(exc))
        with self._lock:
            self._installing.pop(pack_id, None)
            self._errors[pack_id] = error
        entry = self._registry.get(pack_id)
        name = entry["display_name"] if entry is not None else pack_id
        self._publish_state(
            pack_id, "failed", message=f"Installing {name} failed: {exc}", error=error
        )

    @staticmethod
    def _partial_size(staging: Path, pin: PackFilePin) -> int:
        part = partial_path(staging, pin)
        return part.stat().st_size if part.exists() else 0

    @staticmethod
    def _place_files(
        files: list[PackFilePin], parts: list[Path], target: Path
    ) -> list[ManifestFile]:
        recorded: list[ManifestFile] = []
        for pin, part in zip(files, parts, strict=True):
            destination = target / pin["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, destination)
            recorded.append(ManifestFile(path=pin["path"], sha256=pin["sha256"], size=pin["size"]))
        return recorded

    @classmethod
    def _extract_wheels(cls, parts: list[Path], target: Path) -> list[ManifestFile]:
        """Extract the complete nvidia/*/bin DLL set; delete the archives.

        The COMPLETE set is deliberate (spike: faster-whisper #1279) — cuDNN
        9 is split into DLLs that load each other, and a trimmed subset
        fails at model load, not at install.
        """
        recorded: list[ManifestFile] = []
        for part in parts:
            with zipfile.ZipFile(part) as wheel:
                for member in wheel.namelist():
                    if not _DLL_MEMBER_RE.fullmatch(member):
                        continue
                    name = member.rsplit("/", 1)[1]
                    destination = target / name
                    tmp = destination.with_suffix(destination.suffix + ".tmp")
                    with wheel.open(member) as src, tmp.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(tmp, destination)
                    recorded.append(
                        ManifestFile(
                            path=name,
                            sha256=_file_sha256(destination),
                            size=destination.stat().st_size,
                        )
                    )
            part.unlink()  # archives deleted after extraction
        if not recorded:
            raise PackDownloadError("no runtime DLLs found in the downloaded wheels")
        return recorded

    @staticmethod
    def _remove_files(pack_dir_path: Path, manifest: PackManifest) -> None:
        """Delete manifest-listed files (called strictly AFTER the manifest).

        Per-file failures (e.g. a Windows PermissionError on an in-use DLL)
        log and continue: the pack is already uninstalled once the manifest
        is gone, and the leftover bytes are reclaimed on reinstall or a
        later uninstall sweep — a 500 here would misreport a done uninstall.
        """
        for recorded in manifest["files"]:
            path = pack_dir_path / recorded["path"]
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove uninstalled pack file %s: %s", path, exc)

    # -- events ------------------------------------------------------------

    def _publish_state(
        self,
        pack_id: str,
        state: PackState,
        *,
        message: str,
        error: PackInstallError | None = None,
    ) -> None:
        # Per Q5: pack events carry pack_id and MUST NOT carry job_id —
        # job_id presence is the renderer's job/pack discriminator.
        data: dict[str, Any] = {"pack_id": pack_id, "state": state}
        if error is not None:
            data["error"] = error
        self._bus.publish(PipelineEvent(kind="pack_state", step=None, message=message, data=data))

    def _publish_progress(self, pack_id: str, done: int, total: int) -> None:
        self._bus.publish(
            PipelineEvent(
                kind="pack_progress",
                step=None,
                message="",
                data={"pack_id": pack_id, "bytes": done, "total": total},
            )
        )


_DLL_MEMBER_RE = re.compile(r"nvidia/(?:cublas|cudnn)/bin/[^/]+\.dll", re.IGNORECASE)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
