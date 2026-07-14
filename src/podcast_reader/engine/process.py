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
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import uvicorn

from podcast_reader.engine import library
from podcast_reader.engine.app import create_app
from podcast_reader.engine.cookies import resolve_jar_for_source
from podcast_reader.engine.events import EventBus
from podcast_reader.engine.jobs import JobStore
from podcast_reader.engine.managed_tools import (
    export_tools_dir,
    maybe_self_update_ytdlp,
    seed_tools,
)
from podcast_reader.engine.media import MediaManager
from podcast_reader.engine.pack_manager import PackManager
from podcast_reader.engine.settings import (
    atomic_write_json,
    data_dir,
    engine_version,
    load_engine_state,
    load_settings,
    save_engine_state,
    token_fingerprint,
)
from podcast_reader.pipeline import InputType, PipelineError, classify_input, run_pipeline
from podcast_reader.providers import build_provider_registry, canonicalize_custom_providers
from podcast_reader.tools import (
    kill_children,
    popen_kwargs,  # re-export: spawn-time child options
)
from podcast_reader.types import JobModels, LibraryEntry, PipelineRequest, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from podcast_reader.engine.jobs import JobRunner
    from podcast_reader.engine.settings import EngineState
    from podcast_reader.types import JobOverrides, JobRecord, PipelineEvent

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


def make_pipeline_runner(base: Path, key_store: dict[str, str] | None = None) -> JobRunner:
    """Build the engine's job runner: shared pipeline + managed library.

    Settings are snapshotted when the job is dequeued (each invocation reloads
    them), so a mid-job ``PUT /v1/settings`` cannot race the worker. Artifacts
    are produced in the entry's staging directory — which doubles as the cache
    for re-submissions — and committed into the entry directory atomically.

    *key_store* is the process-memory chapter-API-key dict shared with the
    FastAPI app (``PUT /v1/keys`` writes it); the runner injects the key for
    the configured built-in or named provider at dequeue, falling back to its
    established or deterministic per-name environment variable.
    """
    keys: dict[str, str] = key_store if key_store is not None else {}

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

        # Rerun overrides (model picker): merge over the settings snapshot and
        # clear exactly the cached artifacts the change invalidates (the staging
        # dir doubles as the artifact cache), so a chapter-only rerun doesn't
        # re-transcribe and neither re-downloads the source audio.
        overrides = record.get("overrides") or {}
        _clear_rerun_artifacts(staging, overrides)
        # An explicit chapter_model override wins even when "" (force the
        # provider default); only fall back to the setting when not overridden.
        chapter_model = (
            (overrides["chapter_model"] or None)
            if "chapter_model" in overrides
            else (settings["chapter_model"] or None)
        )

        # Jar-aware download (cookie-management spec): a stored jar whose
        # domain suffix-matches the source host wins over the YT_DLP_COOKIES
        # env fallback, which still applies when no jar matches.
        jar = resolve_jar_for_source(base, source)
        provider = overrides.get("chapter_provider") or settings["chapter_provider"]
        whisper_model = overrides.get("whisper_model") or settings["whisper_model"]
        custom_providers = canonicalize_custom_providers(settings["custom_providers"])
        chapter_api_key = _resolve_chapter_key(provider, keys, custom_providers)
        # Record only the model claims that actually applied to this job. The
        # app renders null fields as absent rows, so skipped steps do not leave
        # behind stale provider/model strings.
        input_type = classify_input(source)
        record["models"] = JobModels(
            whisper_model=None if input_type == InputType.YOUTUBE else whisper_model,
            chapter_provider=provider if chapter_api_key else None,
            chapter_model=(chapter_model if chapter_api_key else None) or None,
        )
        request = PipelineRequest(
            source=record["source"],
            title=record["title"],
            output_dir=str(staging),
            model=chapter_model,
            whisper_model=whisper_model,
            whisper_lang=settings["whisper_lang"],
            whisper_device=settings["whisper_device"],
            hf_token=os.environ.get("HF_TOKEN"),
            sentences=settings["sentences"],
            cookies=str(jar) if jar is not None else os.environ.get("YT_DLP_COOKIES"),
            chapter_provider=provider,
            chapter_api_key=chapter_api_key,
            custom_provider_url=overrides.get("custom_provider_url")
            or settings["custom_provider_url"],
            custom_providers=custom_providers,
            diarize=settings["diarize"],
            caption_cleanup=settings["caption_cleanup"],
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


def _clear_rerun_artifacts(staging: Path, overrides: JobOverrides) -> None:
    """Delete the cached artifacts a rerun override invalidates so the pipeline
    regenerates them (the staging dir is the artifact cache).

    A ``whisper_model`` change invalidates the transcript: drop every ``*.json``
    (whisper output + chapters) and the ``*.html`` render, forcing a
    re-transcribe — but keep any downloaded source audio so it isn't re-fetched.
    A chapter-only change keeps the whisper JSON and drops ``*_chapters.json``,
    ``*_caption_cleanup.json``, and ``*.html`` (re-chapter + re-render, no
    re-transcribe). With no overrides, nothing is cleared (a plain
    re-submission still reuses the cache).
    """
    if overrides.get("whisper_model"):
        _unlink_all(staging.glob("*.json"))
        _unlink_all(staging.glob("*.html"))
    elif any(
        overrides.get(k) for k in ("chapter_provider", "chapter_model", "custom_provider_url")
    ):
        _unlink_all(staging.glob("*_chapters.json"))
        _unlink_all(staging.glob("*_caption_cleanup.json"))
        _unlink_all(staging.glob("*.html"))


def _unlink_all(paths: Iterable[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _resolve_chapter_key(
    provider: str,
    keys: dict[str, str],
    custom_providers: Sequence[Mapping[str, object]] = (),
) -> str | None:
    """Resolve the chapter API key: pushed key first, then the env variable.

    The env fallback preserves headless ``podcast-reader serve`` deployments
    that export ``ANTHROPIC_API_KEY`` today; a key pushed via ``PUT /v1/keys``
    takes precedence. An unknown provider resolves to no key, which the
    pipeline degrades to a ``chapters_skipped`` warning.
    """
    pushed = keys.get(provider)
    if pushed:
        return pushed
    spec = build_provider_registry(custom_providers).get(provider)
    if spec is None:
        return None
    return os.environ.get(spec["key_env"])


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
    # Reconcile bundle tool seeds into <data_dir>/tools (newer wins) and make
    # that dir the effective default for every resolve_tool call site — both
    # before anything can spawn a tool (tools-seeding spec). Seeding failures
    # log and continue: the engine serves regardless.
    seed_tools(base)
    export_tools_dir(base)
    state = load_engine_state(base)
    sock = bind_engine_socket(base, state)
    if sys.platform == "win32":
        _join_windows_job()
    # One process-memory key store shared by the job runner and the app
    # (the runner closure is built before the app, so app state can't host it).
    key_store: dict[str, str] = {}
    # One EventBus shared by the job store and the pack manager (the public
    # publish seam, per S6): job and pack events ride the same SSE stream.
    bus = EventBus()
    store = JobStore(base, make_pipeline_runner(base, key_store), bus=bus)
    pack_manager = PackManager(base, bus=bus)
    # Startup pack validation (per S8/F13): flag incompatible or damaged
    # installed packs before serving; the same checks back every GET /v1/packs.
    pack_manager.validate_installed()
    # Media core (floating-video-player): shares the same EventBus so media-prep
    # events ride the SSE stream. get_entry reads the current library_dir each
    # call (the setting can change at runtime), mirroring the route helpers.
    media_manager = MediaManager(
        data_dir=base,
        bus=bus,
        # A live resolver, not a snapshot: a PUT /v1/settings change to the cap
        # takes effect without a restart (read fresh on each eviction).
        cache_max_bytes=lambda: load_settings(base)["media_cache_max_bytes"],
        get_entry=lambda sid: library.get_entry(Path(load_settings(base)["library_dir"]), sid),
    )

    # POST /v1/shutdown hook: the server object is created after the app, so
    # the hook reaches it through this list (filled before any request runs).
    servers: list[uvicorn.Server] = []

    def request_shutdown() -> None:
        for srv in servers:
            srv.should_exit = True

    app = create_app(
        base,
        store,
        key_store=key_store,
        on_shutdown=request_shutdown,
        pack_manager=pack_manager,
        media_manager=media_manager,
    )
    # timeout_graceful_shutdown bounds exit even when a client leaves an SSE
    # stream open — an open /v1/events response would otherwise hold graceful
    # shutdown forever (per P1).
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", timeout_graceful_shutdown=3))
    servers.append(server)
    if on_server is not None:
        on_server(server)
    path = discovery_file if discovery_file is not None else base / DISCOVERY_FILE
    try:
        store.start_worker()
        pack_manager.start_worker()
        # Scheduled yt-dlp self-update (design decision 8): background thread,
        # gated inside on the 24 h cadence and the user-data residence of the
        # resolved binary; never touches PATH/pip copies, never raises.
        threading.Thread(
            target=maybe_self_update_ytdlp, args=(base,), name="ytdlp-self-update", daemon=True
        ).start()
        write_discovery(path, state, sock)
        server.run(sockets=[sock])
    finally:
        remove_discovery(path)
        # Mark the store stopping BEFORE reaping children: the in-flight job
        # fails when its child dies, and that failure must already be
        # attributable to shutdown (journaled interrupted, per P2).
        store.begin_shutdown()
        kill_children()  # unblock the worker so store.shutdown() can join it
        store.shutdown()
        # An in-flight pack download aborts between chunks; its partial stays
        # on disk, so the pack surfaces as resumable on the next start.
        pack_manager.shutdown()
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
            error = ctypes.WinError(ctypes.get_last_error())
            _kernel32.CloseHandle(job)
            raise error
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
