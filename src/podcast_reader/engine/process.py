"""Engine process model: pre-bound socket, discovery handshake, child reaping, serve.

Startup handshake (per design decision 6): bind the socket first (persisted
port, or port 0 on first run), persist the real port, write the discovery file
atomically with mode 0600, print the ready sentinel, then hand the pre-bound
socket to uvicorn. The advertised port is therefore live before any client
reads the discovery file — no probe-the-port retry loop, no TOCTOU.

Child reaping: POSIX children run in their own session and are registered in
``tools``' child registry by ``tools.run_child``; shutdown calls
``tools.kill_children`` to SIGTERM each live process group. On Windows the
engine joins a Job Object with kill-on-job-close at startup so children
(which inherit job membership) die with the engine.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import uvicorn

from podcast_reader.engine import library
from podcast_reader.engine.app import create_app
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.settings import (
    atomic_write_json,
    data_dir,
    engine_version,
    load_engine_state,
    load_settings,
    save_engine_state,
    token_fingerprint,
)
from podcast_reader.pipeline import PipelineError, run_pipeline
from podcast_reader.tools import (
    kill_children,
    popen_kwargs,  # re-export: spawn-time child options
)
from podcast_reader.types import LibraryEntry, PipelineRequest, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.engine.jobs import JobRunner
    from podcast_reader.engine.settings import EngineState
    from podcast_reader.types import JobRecord, PipelineEvent

__all__ = [
    "DiscoveryInfo",
    "READY_SENTINEL",
    "bind_engine_socket",
    "bind_socket_option",
    "make_pipeline_runner",
    "popen_kwargs",
    "remove_discovery",
    "serve_engine",
    "write_discovery",
]

logger = logging.getLogger(__name__)

DISCOVERY_FILE = "engine.json"
READY_SENTINEL = "PODCAST_READER_READY"


class DiscoveryInfo(TypedDict):
    """Contents of the discovery file a supervisor reads to adopt the engine."""

    port: int
    pid: int
    token_fingerprint: str
    version: str


def bind_socket_option(platform: str = sys.platform) -> int:
    """``SOL_SOCKET`` option for the engine bind, selected per platform.

    POSIX: ``SO_REUSEADDR`` allows immediate rebinding of a ``TIME_WAIT`` port
    while a live listener still fails ``EADDRINUSE`` (preserving the
    ephemeral-port fallback). Windows: ``SO_REUSEADDR`` there allows binding an
    actively-bound port, which would defeat the fallback — use
    ``SO_EXCLUSIVEADDRUSE`` instead.
    """
    if platform == "win32":
        # Only Windows builds of CPython expose socket.SO_EXCLUSIVEADDRUSE;
        # its winsock value is ~SO_REUSEADDR == ~4 (WinSock2.h).
        return int(getattr(socket, "SO_EXCLUSIVEADDRUSE", ~4))
    return int(socket.SO_REUSEADDR)


def bind_engine_socket(base: Path, state: EngineState) -> socket.socket:
    """Bind (and listen on) the engine socket, persisting the real port.

    Tries the persisted port first; on conflict or first run (port 0) binds an
    ephemeral port and saves it so subsequent starts reuse it. The socket is
    already listening when returned, so connections queue in the backlog until
    uvicorn starts accepting — the advertised port is live immediately.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, bind_socket_option(), 1)
    try:
        sock.bind(("127.0.0.1", state["port"]))
    except OSError:
        sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])
    if port != state["port"]:
        state["port"] = port
        save_engine_state(base, state)
    return sock


def write_discovery(path: Path, state: EngineState, sock: socket.socket) -> None:
    """Atomically write the discovery file (0600), then print the ready sentinel.

    The sentinel is printed strictly after the file is on disk, so a supervisor
    that waits for the sentinel can immediately read a complete discovery file.
    """
    info = DiscoveryInfo(
        port=int(sock.getsockname()[1]),
        pid=os.getpid(),
        token_fingerprint=token_fingerprint(state["token"]),
        version=engine_version(),
    )
    atomic_write_json(path, info, mode=0o600)
    print(READY_SENTINEL, flush=True)


def remove_discovery(path: Path) -> None:
    """Remove the discovery file on clean shutdown (idempotent)."""
    path.unlink(missing_ok=True)


def make_pipeline_runner(base: Path) -> JobRunner:
    """Build the engine's job runner: shared pipeline + managed library.

    Settings are snapshotted when the job is dequeued (each invocation reloads
    them), so a mid-job ``PUT /v1/settings`` cannot race the worker. Artifacts
    are produced in the entry's staging directory — which doubles as the cache
    for re-submissions — and committed into the entry directory atomically.
    """

    def run(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
        settings = load_settings(base)  # snapshot at dequeue
        library_dir = Path(settings["library_dir"])
        source = record["source"]
        # Stat-check local sources before hashing them for identity, so a bad
        # path fails structured ("not_found"), not as an internal error.
        if not source.startswith(("http://", "https://")) and not Path(source).is_file():
            raise PipelineError(
                "not_found",
                f"File not found: {source}",
                "Check the path and try again.",
            )
        source_id = library.source_identity(source)
        staging = library.staging_dir(library_dir, source_id)
        staging.mkdir(parents=True, exist_ok=True)

        request = PipelineRequest(
            source=record["source"],
            title=record["title"],
            output_dir=str(staging),
            model=settings["chapter_model"],
            whisper_model=settings["whisper_model"],
            whisper_lang=settings["whisper_lang"],
            whisper_device=settings["whisper_device"],
            hf_token=os.environ.get("HF_TOKEN"),
            sentences=settings["sentences"],
            cookies=os.environ.get("YT_DLP_COOKIES"),
            chapter_provider="anthropic",
            chapter_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            custom_provider_url="",
        )
        staged = run_pipeline(request, on_event)
        result = _commit_artifacts(staged, library.entry_dir(library_dir, source_id))
        library.add_entry(
            library_dir,
            LibraryEntry(
                source_id=source_id,
                source=record["source"],
                title=result["title"],
                html_path=result["html_path"],
                created_at=time.time(),
            ),
        )
        return result

    return run


def _commit_artifacts(staged: PipelineResult, entry: Path) -> PipelineResult:
    """Publish staged artifacts into the entry dir, returning the final paths.

    Each artifact is committed atomically (``library.stage_and_commit``); the
    staging copies are kept as the cache for re-submissions.
    """
    entry.mkdir(parents=True, exist_ok=True)

    def commit(staged_path: str) -> str:
        final = entry / Path(staged_path).name
        library.stage_and_commit(Path(staged_path), final)
        return str(final)

    chapters_path = staged["chapters_path"]
    return PipelineResult(
        json_path=commit(staged["json_path"]),
        chapters_path=commit(chapters_path) if chapters_path is not None else None,
        html_path=commit(staged["html_path"]),
        title=staged["title"],
    )


def serve_engine(
    *,
    discovery_file: Path | None = None,
    on_server: Callable[[uvicorn.Server], None] | None = None,
) -> None:
    """Bind the engine socket, write the discovery file, and serve the API.

    *on_server* receives the uvicorn server before it runs (tests use it to
    trigger a clean shutdown via ``server.should_exit``).
    """
    base = data_dir()
    state = load_engine_state(base)
    sock = bind_engine_socket(base, state)
    if sys.platform == "win32":
        _join_windows_job()
    store = JobStore(base, make_pipeline_runner(base))
    app = create_app(base, store)
    server = uvicorn.Server(uvicorn.Config(app, log_level="info"))
    if on_server is not None:
        on_server(server)
    path = discovery_file if discovery_file is not None else base / DISCOVERY_FILE
    try:
        store.start_worker()
        write_discovery(path, state, sock)
        server.run(sockets=[sock])
    finally:
        remove_discovery(path)
        kill_children()  # unblock the worker so store.shutdown() can join it
        store.shutdown()
        sock.close()


if sys.platform == "win32":  # pragma: no cover — exercised on Windows only
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    # Declare restype/argtypes explicitly: ctypes defaults every result to a
    # C int, which truncates 64-bit HANDLEs on 64-bit Windows.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,  # hJob
        ctypes.c_int,  # JOBOBJECTINFOCLASS
        wintypes.LPVOID,  # lpJobObjectInformation
        wintypes.DWORD,  # cbJobObjectInformationLength
    )
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _windows_job() -> int:
        """Create a Job Object whose processes are killed when its handle closes."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(job)

    _job_handle: int | None = None

    def _join_windows_job() -> None:
        """Assign this engine process to a kill-on-close job; children inherit it.

        Failure is non-fatal by design (availability over strictness): when
        the engine already runs inside a Job Object without nested-job
        support — common under CI runners and some terminals — assignment
        fails, and refusing to start would make the engine unusable exactly
        where it is most often launched. The engine serves anyway; child
        reaping on Windows degrades to best-effort for that run.
        """
        global _job_handle
        if _job_handle is not None:
            return
        try:
            job = _windows_job()
        except OSError as exc:
            logger.warning(
                "Job Object setup failed; children will not be force-reaped on exit: %s", exc
            )
            return
        if not _kernel32.AssignProcessToJobObject(job, _kernel32.GetCurrentProcess()):
            exc = ctypes.WinError(ctypes.get_last_error())
            _kernel32.CloseHandle(job)
            logger.warning(
                "AssignProcessToJobObject failed (already inside a Job Object?); "
                "children will not be force-reaped on exit: %s",
                exc,
            )
            return
        _job_handle = job  # keep the handle alive for the engine's lifetime
