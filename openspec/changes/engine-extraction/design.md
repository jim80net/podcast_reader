# Engine Extraction — Design

## Context

Parent design: `docs/superpowers/specs/2026-06-11-desktop-packaging-design.md` (v3, systems-reviewed twice). This change implements its Phase 1. Current state: `cli._run_pipeline` (`cli.py:111`) is a linear function calling `youtube.py` / `ytdlp.py` / `transcribe.py` / `chapters.py` / `html.py`, printing progress, exiting on error, writing artifacts to cwd with skip-if-exists caching. Tests mock subprocess boundaries; mypy strict; ruff.

## Goals / Non-Goals

**Goals:**
- One pipeline implementation serving both CLI one-shot mode and the engine's job runner.
- Engine API exactly as specified in the parent design's table (v1 prefix, header-only auth).
- Process model an Electron app can rely on: discovery file, fixed port, child reaping, adopt-or-kill.
- Packaging spike answers: frozen layout, whisper worker invocation, diarization go/no-go, installer size.

**Non-Goals:**
- Electron app, extension, download manager UI, multi-provider chapters (Phases 2-5).
- Engine settings for API keys beyond the in-memory push endpoint shape (Phase 2 fills it).
- Windows/macOS CI runners (tag pipelines come with Phase 3); the spike builds Linux + local-Windows evidence.

## Decisions

1. **Pipeline as a step list, events as a callback.** `pipeline.py` exposes `run_pipeline(request, on_event: Callable[[PipelineEvent], None])` with steps as small functions returning typed results. CLI passes a print-adapter; engine passes a job-store adapter. Rationale: one orchestration, two faces; avoids engine importing from `cli` or vice versa (both import `pipeline`). Alternative (engine wraps CLI subprocess) rejected: loses typed progress and doubles process management.
2. **TypedDict/dataclass boundary types first** (per repo convention and strict mypy): `PipelineRequest`, `PipelineEvent`, `JobRecord`, `LibraryEntry`, `EngineSettings` defined before handlers.
3. **Job execution: single worker thread + queue in-process.** Transcription is GPU/CPU-bound and serial by nature on one machine; a `queue.Queue` + one worker thread keeps SSE simple (no multiprocessing IPC). Concurrency cap = 1 job running; others queued. Alternative (asyncio task per job) rejected: pipeline steps are blocking subprocess calls; a thread isolates them from the event loop.
4. **SSE via fastapi StreamingResponse fed from an in-memory per-client queue**; events also persisted onto the JobRecord so `GET /jobs/{id}` is always the source of truth (extension hydration pattern from parent design).
5. **Auth:** middleware checks `Authorization: Bearer <token>` on every route except none (health included — health leaks version otherwise). Token generated at first `serve`, stored 0600 in `<data_dir>/token`, fingerprint (sha256 prefix) in the discovery file. No query-param fallback.
6. **Discovery file + port:** first `serve` picks a free port, persists it in `<data_dir>/settings.json`; discovery file written atomically next to it on every start; removed on clean shutdown. Stale detection: PID alive check + `/v1/health` token probe.
7. **Child reaping:** `subprocess.Popen(..., creationflags=CREATE_SUSPENDED?)` — no: Windows Job Objects via `pywin32`-free ctypes helper (assign child on spawn, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`); POSIX `start_new_session=True` + `os.killpg` on shutdown. Encapsulated in `engine/process.py` so `transcribe.py`/`ytdlp.py` call sites stay clean.
8. **Library keying:** `source_id = sha256(url)` for URLs, `sha256(file bytes)` for local files; artifacts live under `<library>/<source_id[:12]>/`. Index `library.json` written temp+`os.replace`. Cache hit = artifact exists AND validates (json parses / html non-empty); validation failure deletes and recomputes.
9. **CLI compatibility:** `main()` keeps argv shape; one-shot mode calls `run_pipeline` with the print adapter and writes to cwd exactly as today (loose files, stem naming) — the managed library is engine-only. `serve` subcommand starts uvicorn programmatically.
10. **Chapters fault isolation:** `pipeline.py` wraps the chapters step; any exception → `PipelineEvent(level=warning, code=chapters_failed)` + proceed. Applies to CLI automatically by sharing the step runner.
11. **Spike is a sibling deliverable, not production code:** `spike/` directory with PyInstaller spec files, a build script, and `SPIKE_REPORT.md` recording: onedir layout, worker entry point invocation, freeze-aware `resolve_tool` evidence, CUDA DLL dir injection mechanism, diarization sizing + merge-glue interface sketch, measured sizes. Production `tools.py` changes land from its findings; the spike artifacts themselves are not shipped.

## Risks / Trade-offs

- [Single-thread job worker serializes jobs] → acceptable v1; design leaves room for a pool later (queue abstraction).
- [ctypes Job Object code is fiddly] → isolated module + lifecycle tests; POSIX path is simple and covers CI.
- [faster-whisper in-process in the worker may diverge from whisper-ctranslate2 CLI output shape] → spike validates JSON parity against the existing fixture; engine keeps subprocess boundary either way.
- [SSE clients leak queues] → per-client queue with disconnect cleanup; bounded queue size.
- [fastapi/uvicorn become core deps, growing CLI install] → acceptable; they're pure-python wheels, and `uv tool install` users get `serve` for free (extension/app reuse later).

## Migration Plan

Pure addition + internal refactor; CLI behavior unchanged (verified by existing tests). No data migration (library is new). Rollback = revert PR.

## Open Questions

- Whether the whisper worker (frozen second entry point) speaks JSON-over-stdout or writes files like today — spike decides; engine treats it as the existing `transcribe()` boundary either way.
- Diarization go/no-go — spike decides against the parent design's cut-line.
