"""Engine process model: pre-bound socket, discovery handshake, child reaping, serve.

Startup handshake (per design decision 6): bind the socket first (persisted
port, or port 0 on first run), persist the real port, write the discovery file
atomically with mode 0600, print the ready sentinel, then hand the pre-bound
socket to uvicorn. The advertised port is therefore live before any client
reads the discovery file — no probe-the-port retry loop, no TOCTOU.

Child reaping: POSIX children run in their own session
(``tools.popen_kwargs``); on Windows the engine joins a Job Object with
kill-on-job-close at startup so children (which inherit job membership) die
with the engine.
"""

from __future__ import annotations

import json
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
    data_dir,
    engine_version,
    load_engine_state,
    load_settings,
    save_engine_state,
    token_fingerprint,
)
from podcast_reader.pipeline import run_pipeline
from podcast_reader.tools import popen_kwargs  # re-export: spawn-time child options
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
    "make_pipeline_runner",
    "popen_kwargs",
    "remove_discovery",
    "serve_engine",
    "write_discovery",
]

DISCOVERY_FILE = "engine.json"
READY_SENTINEL = "PODCAST_READER_READY"


class DiscoveryInfo(TypedDict):
    """Contents of the discovery file a supervisor reads to adopt the engine."""

    port: int
    pid: int
    token_fingerprint: str
    version: str


def bind_engine_socket(base: Path, state: EngineState) -> socket.socket:
    """Bind (and listen on) the engine socket, persisting the real port.

    Tries the persisted port first; on conflict or first run (port 0) binds an
    ephemeral port and saves it so subsequent starts reuse it. The socket is
    already listening when returned, so connections queue in the backlog until
    uvicorn starts accepting — the advertised port is live immediately.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(info, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
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
        source_id = library.source_identity(record["source"])
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
    store.start_worker()
    path = discovery_file if discovery_file is not None else base / DISCOVERY_FILE
    write_discovery(path, state, sock)
    try:
        server.run(sockets=[sock])
    finally:
        remove_discovery(path)
        store.shutdown()
        sock.close()


if sys.platform == "win32":  # pragma: no cover — exercised on Windows only
    import ctypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

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
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError("CreateJobObjectW failed")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            raise OSError("SetInformationJobObject failed")
        return int(job)

    _job_handle: int | None = None

    def _join_windows_job() -> None:
        """Assign this engine process to a kill-on-close job; children inherit it."""
        global _job_handle
        if _job_handle is not None:
            return
        kernel32 = ctypes.windll.kernel32
        job = _windows_job()
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            raise OSError("AssignProcessToJobObject failed")
        _job_handle = job  # keep the handle alive for the engine's lifetime
