# Engine Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the CLI pipeline into a shared step runner and wrap it in a localhost FastAPI engine (jobs, SSE progress, managed library, discovery handshake) — Phase 1 of the desktop packaging design.

**Architecture:** `pipeline.py` owns orchestration with typed progress events; `cli.py` becomes a thin adapter (one-shot prints, `serve` starts the engine); `engine/` adds settings, library, job journal/worker, FastAPI app, and process/discovery management. Spec contracts: `openspec/changes/engine-extraction/specs/*/spec.md`; decisions: its `design.md`.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, pytest, mypy --strict, ruff. TypedDict/dataclass-first per repo convention.

**Conventions for every task:** run gates with `timeout 300 uv run pytest -m "not integration" -q`, `timeout 300 uv run mypy src/`, `timeout 120 uv run ruff check src/ tests/ && timeout 120 uv run ruff format src/ tests/`. Commit after each green task with the message given in the task.

---

### Task 0: Dependencies

**Files:**
- Modify: `pyproject.toml` (dependencies)

- [ ] **Step 0.1:** Add to `[project] dependencies`: `"fastapi>=0.111"`, `"uvicorn>=0.30"`. Add to `dev` extra: `"httpx>=0.27"` (TestClient transport). Run `timeout 300 uv sync --extra dev --extra chapters`. Expected: resolves cleanly.
- [ ] **Step 0.2:** Commit: `chore: add fastapi/uvicorn core deps for engine`

### Task 1: Boundary types

**Files:**
- Create: `src/podcast_reader/types.py`
- Test: `tests/test_types.py`

- [ ] **Step 1.1: Failing test**

```python
"""Tests for podcast_reader.types."""
from __future__ import annotations

from podcast_reader.types import JOB_STATES, PipelineEvent, new_job_record


def test_pipeline_event_shape() -> None:
    e = PipelineEvent(kind="step_started", step="transcribe", message="", data={})
    assert e["kind"] == "step_started"


def test_new_job_record_defaults() -> None:
    rec = new_job_record(job_id="j1", source="https://x", title=None)
    assert rec["state"] == "queued"
    assert rec["events"] == []
    assert set(JOB_STATES) >= {"queued", "running", "done", "failed", "interrupted", "awaiting-confirmation"}
```

- [ ] **Step 1.2:** Run `timeout 120 uv run pytest tests/test_types.py -q` — expect FAIL (module missing).
- [ ] **Step 1.3: Implement `src/podcast_reader/types.py`**

```python
"""Typed boundaries shared by the pipeline, CLI, and engine."""
from __future__ import annotations

from typing import Any, Literal, TypedDict

StepName = Literal["resolve", "captions", "download", "transcribe", "chapters", "render"]
EventKind = Literal["step_started", "step_finished", "warning", "job_done", "job_failed"]
JobState = Literal["queued", "awaiting-confirmation", "running", "done", "failed", "interrupted"]

JOB_STATES: tuple[JobState, ...] = (
    "queued", "awaiting-confirmation", "running", "done", "failed", "interrupted",
)


class PipelineEvent(TypedDict):
    kind: EventKind
    step: StepName | None
    message: str
    data: dict[str, Any]


class JobError(TypedDict):
    code: str
    message: str
    hint: str


class PipelineRequest(TypedDict):
    source: str  # URL or local file path
    title: str | None
    output_dir: str
    model: str
    whisper_model: str
    whisper_lang: str
    whisper_device: str
    hf_token: str | None
    sentences: int
    cookies: str | None


class PipelineResult(TypedDict):
    json_path: str
    chapters_path: str | None
    html_path: str
    title: str


class JobRecord(TypedDict):
    id: str
    source: str
    title: str | None
    state: JobState
    error: JobError | None
    events: list[PipelineEvent]
    result: PipelineResult | None
    created_at: float
    updated_at: float


class LibraryEntry(TypedDict):
    source_id: str
    source: str
    title: str
    html_path: str
    created_at: float


class EngineSettings(TypedDict):
    whisper_model: str
    whisper_lang: str
    whisper_device: str
    sentences: int
    library_dir: str
    chapter_model: str


def new_job_record(*, job_id: str, source: str, title: str | None) -> JobRecord:
    """Create a queued JobRecord with empty history (timestamps set by the store)."""
    return JobRecord(
        id=job_id, source=source, title=title, state="queued", error=None,
        events=[], result=None, created_at=0.0, updated_at=0.0,
    )
```

- [ ] **Step 1.4:** Run the test — expect PASS. Run mypy/ruff gates.
- [ ] **Step 1.5:** Commit: `feat(engine): add typed pipeline/job/library boundaries`

### Task 2: Pipeline extraction with events + chapters fault isolation

**Files:**
- Create: `src/podcast_reader/pipeline.py`
- Modify: `src/podcast_reader/cli.py` (delete `_run_pipeline`, `_transcribe_if_needed`, `_find_ytdlp_marker`, `classify_input`, `InputType` — they move)
- Test: `tests/test_pipeline.py` (port of `tests/test_cli.py` pipeline classes), keep `tests/test_cli.py` for argv-level tests

- [ ] **Step 2.1: Port tests.** Copy `tests/test_cli.py` to `tests/test_pipeline.py`. In the copy: change import to `from podcast_reader.pipeline import InputType, _find_ytdlp_marker, run_pipeline, classify_input`; change every `@patch("podcast_reader.cli.X")` to `@patch("podcast_reader.pipeline.X")`; replace `_run_pipeline(**kwargs)` calls with `run_pipeline(_request(**kwargs), on_event=lambda e: None)` where `_request` builds a `PipelineRequest` from the old `_pipeline_defaults` fields (`output_dir`/`cookies` become `str`). Keep every assertion. Remove pipeline test classes from `tests/test_cli.py`, keeping `TestClassifyInput` pointing at `podcast_reader.pipeline`.
- [ ] **Step 2.2: Add the new fault-isolation + event tests to `tests/test_pipeline.py`:**

```python
class TestChaptersFaultIsolation:
    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.generate_chapters", side_effect=RuntimeError("provider down"))
    @patch("podcast_reader.pipeline.format_transcript", return_value="[0.0] Hi.")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_chapter_failure_still_renders_html(
        self, _s: MagicMock, _f: MagicMock, _fmt: MagicMock, _gen: MagicMock,
        mock_build_html: MagicMock, _w: MagicMock, tmp_path: Path,
    ) -> None:
        events: list[PipelineEvent] = []
        run_pipeline(_request(input_arg=_YT_URL, output_dir=tmp_path), on_event=events.append)
        assert (tmp_path / "abc123XYZqq.html").exists()
        assert any(e["kind"] == "warning" and e["data"].get("code") == "chapters_failed" for e in events)
        assert mock_build_html.call_args.kwargs["chapters"] is None


class TestEvents:
    @patch("podcast_reader.pipeline._wsl_path", return_value=None)
    @patch("podcast_reader.pipeline.build_html", return_value="<html></html>")
    @patch("podcast_reader.pipeline.fetch_transcript")
    @patch("podcast_reader.pipeline.snippets_to_whisper_segments", return_value=_SAMPLE_SEGMENTS)
    def test_step_events_emitted_in_order(
        self, _s: MagicMock, _f: MagicMock, _b: MagicMock, _w: MagicMock,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        events: list[PipelineEvent] = []
        run_pipeline(_request(input_arg=_YT_URL, output_dir=tmp_path), on_event=events.append)
        started = [e["step"] for e in events if e["kind"] == "step_started"]
        assert started[0] == "resolve" and "render" in started
        assert events[-1]["kind"] == "job_done"
```

(`_YT_URL = "https://www.youtube.com/watch?v=abc123XYZqq"`; `_request` helper returns a `PipelineRequest`.)
- [ ] **Step 2.3:** Run `timeout 120 uv run pytest tests/test_pipeline.py -q` — expect FAIL (no module).
- [ ] **Step 2.4: Implement `src/podcast_reader/pipeline.py`.** Move the bodies of `classify_input`, `InputType`, `_YT_URL_RE`, `_wsl_path`, `_find_ytdlp_marker`, `_transcribe_if_needed`, and `_run_pipeline` from `cli.py` verbatim, then: rename `_run_pipeline` → `run_pipeline(request: PipelineRequest, on_event: Callable[[PipelineEvent], None]) -> PipelineResult`; unpack request fields where parameters were; replace each `print(...)` progress line with `_emit(on_event, kind, step, message, data)` (keep messages — the CLI adapter prints them); wrap each phase in `step_started`/`step_finished` events for steps `resolve`, `captions`/`download`+`transcribe`, `chapters`, `render`; wrap the chapters block:

```python
chapters: list[dict[str, Any]] | None = None
if chapters_path.exists():
    ...load as today...
elif os.environ.get("ANTHROPIC_API_KEY"):
    _emit(on_event, "step_started", "chapters", "Generating chapter markers...", {})
    try:
        ...generate/snap/write as today...
    except Exception as exc:  # provider/parse/network — never fatal (spec: chapters fault isolation)
        chapters = None
        _emit(on_event, "warning", "chapters", f"Chapter generation failed: {exc}",
              {"code": "chapters_failed"})
    else:
        _emit(on_event, "step_finished", "chapters", f"{len(chapters)} chapters", {})
```

End with `_emit(on_event, "job_done", None, "Done", {})` and `return PipelineResult(...)`. `sys.exit(1)` paths become `PipelineError(code, message, hint)` raises (new exception in `pipeline.py`); the CLI adapter converts to exit 1. Cache-hit checks gain re-validation here (shared by CLI and engine): add `_valid_artifact(path: Path) -> bool` in `pipeline.py` (JSON parses for `.json`, non-empty for `.html`; invalid → unlink + treat as miss) and use it in every `*.exists()` cache check; add a corrupt-JSON-cache test to `tests/test_pipeline.py` (truncated `{stem}.json` → re-fetch/re-transcribe path runs, job completes). Task 6's `validate_artifact` imports this function rather than duplicating it.
- [ ] **Step 2.5:** Run `timeout 300 uv run pytest tests/test_pipeline.py -q` — expect PASS.
- [ ] **Step 2.6:** Commit: `refactor: extract shared pipeline with typed progress events`

### Task 3: CLI rewire (one-shot adapter + serve stub)

**Files:**
- Modify: `src/podcast_reader/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 3.1: Failing tests** (replace pipeline classes removed in 2.1):

```python
class TestCliAdapter:
    @patch("podcast_reader.cli.run_pipeline")
    def test_one_shot_invokes_pipeline_and_prints(self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        def fake(req: PipelineRequest, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
            on_event(PipelineEvent(kind="step_started", step="resolve", message="Resolving...", data={}))
            return PipelineResult(json_path="a.json", chapters_path=None, html_path="a.html", title="T")
        mock_run.side_effect = fake
        main_with_args(["https://example.com/x.mp3", "T"])
        out = capsys.readouterr().out
        assert "Resolving..." in out and "a.html" in out

    @patch("podcast_reader.cli.run_pipeline", side_effect=PipelineError("not_found", "File not found: /nope", ""))
    def test_one_shot_error_exits_1(self, _m: MagicMock) -> None:
        with pytest.raises(SystemExit, match="1"):
            main_with_args(["/nope"])

    @patch("podcast_reader.cli.serve_engine")
    def test_serve_subcommand_dispatches(self, mock_serve: MagicMock) -> None:
        main_with_args(["serve", "--discovery-file", "/tmp/d.json"])
        mock_serve.assert_called_once()
```

- [ ] **Step 3.2:** Run — expect FAIL (`main_with_args` missing).
- [ ] **Step 3.3: Implement.** In `cli.py`: keep argparse but detect `serve` as first positional (subparsers break the legacy `podcast-reader <url> [title]` shape, so: `if argv and argv[0] == "serve":` use a dedicated serve parser with `--discovery-file`, calling `serve_engine(discovery_file=...)` imported lazily from `podcast_reader.engine.process`; else legacy parser). Extract `main_with_args(argv: list[str]) -> None`; `main()` calls it with `sys.argv[1:]`. One-shot path builds `PipelineRequest`, defines `def _print_event(e: PipelineEvent) -> None: print(e["message"]) if e["message"] else None`, calls `run_pipeline`, prints the result paths (+ `_wsl_path` line), catches `PipelineError` → stderr + `sys.exit(1)`.
- [ ] **Step 3.4:** Run full unit suite + gates — expect PASS (ported pipeline tests + new CLI tests).
- [ ] **Step 3.5:** Commit: `refactor: cli one-shot becomes pipeline adapter; add serve dispatch`

### Task 4: Freeze-aware tool resolution

**Files:**
- Modify: `src/podcast_reader/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 4.1: Failing tests** (add to existing class; spec scenarios):

```python
def test_tools_dir_param_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools_dir = tmp_path / "tools"; tools_dir.mkdir()
    exe = tools_dir / "yt-dlp"; exe.touch(); exe.chmod(0o755)
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    sib = bin_dir / "yt-dlp"; sib.touch(); sib.chmod(0o755)
    (bin_dir / "python").touch()
    monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))
    assert resolve_tool("yt-dlp", tools_dir=tools_dir) == str(exe)

def test_env_var_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools_dir = tmp_path / "tools"; tools_dir.mkdir()
    exe = tools_dir / "yt-dlp"; exe.touch(); exe.chmod(0o755)
    monkeypatch.setenv("PODCAST_READER_TOOLS_DIR", str(tools_dir))
    assert resolve_tool("yt-dlp") == str(exe)

def test_frozen_skips_interpreter_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    sib = bin_dir / "yt-dlp"; sib.touch(); sib.chmod(0o755)
    (bin_dir / "python").touch()
    monkeypatch.setattr("podcast_reader.tools.sys.executable", str(bin_dir / "python"))
    monkeypatch.setattr("podcast_reader.tools.sys", _FrozenSys(str(bin_dir / "python"), str(tmp_path / "bundle")))
    assert resolve_tool("yt-dlp") == "yt-dlp"  # bundle tools dir empty; interpreter dir NOT searched

def test_frozen_bundle_tools_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "bundle"; (bundle / "tools").mkdir(parents=True)
    exe = bundle / "tools" / "yt-dlp"; exe.touch(); exe.chmod(0o755)
    monkeypatch.setattr("podcast_reader.tools.sys", _FrozenSys(str(bundle / "engine.exe"), str(bundle)))
    assert resolve_tool("yt-dlp") == str(exe)
```

(`_FrozenSys` = tiny stand-in object with `executable`, `frozen = True`, `_MEIPASS = bundle`.)
- [ ] **Step 4.2:** Run — expect FAIL (no `tools_dir` param).
- [ ] **Step 4.3: Implement `resolve_tool`:**

```python
def resolve_tool(name: str, tools_dir: Path | None = None) -> str:
    """Resolve *name* per spec precedence: explicit/env tools dir → frozen bundle
    tools dir (or interpreter sibling when unfrozen) → bare name for PATH."""
    if tools_dir is None:
        env = os.environ.get("PODCAST_READER_TOOLS_DIR")
        tools_dir = Path(env) if env else None
    if tools_dir is not None:
        found = shutil.which(name, path=str(tools_dir))
        if found:
            return found
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        found = shutil.which(name, path=str(bundle / "tools"))
        return found if found else name
    found = shutil.which(name, path=str(Path(sys.executable).parent))
    return found if found else name
```

- [ ] **Step 4.4:** Run tests + gates — PASS. Commit: `feat: freeze-aware tool resolution with user tools dir`

### Task 5: Engine settings & state

**Files:**
- Create: `src/podcast_reader/engine/__init__.py` (empty), `src/podcast_reader/engine/settings.py`
- Test: `tests/engine/test_settings.py` (+ empty `tests/engine/__init__.py`)

- [ ] **Step 5.1: Failing tests**

```python
def test_data_dir_env_override(tmp_path, monkeypatch): ...  # PODCAST_READER_DATA_DIR respected
def test_engine_state_created_0600(tmp_path):  # port+token persisted; mode 0600; token len >= 32
def test_engine_state_reused(tmp_path):        # second load returns same port/token
def test_user_settings_roundtrip_atomic(tmp_path):  # save writes temp+replace; load returns saved EngineSettings
def test_default_settings_match_env_defaults(tmp_path):  # whisper_model large-v3 etc.
```

Write them concretely against this API: `data_dir() -> Path` (default `Path.home()/"PodcastReader"`, env `PODCAST_READER_DATA_DIR`), `load_engine_state(data_dir) -> EngineState` (TypedDict `{port: int, token: str}`; creates with `port=0`, `token=secrets.token_urlsafe(32)`, file `engine-state.json` chmod 0600), `save_engine_state`, `load_settings(data_dir) -> EngineSettings`, `save_settings` (temp + `os.replace`, `threading.Lock` module-level).
- [ ] **Step 5.2:** Run — FAIL. Implement `engine/settings.py` exactly to that API (≈70 lines; defaults from current env-var defaults in `cli.py`).
- [ ] **Step 5.3:** Gates PASS. Commit: `feat(engine): settings and engine-state persistence`

### Task 6: Library

**Files:**
- Create: `src/podcast_reader/engine/library.py`
- Test: `tests/engine/test_library.py`

- [ ] **Step 6.1: Failing tests** covering the four spec scenarios: `source_id` is sha256-hex of URL (or of file bytes for local paths); `entry_dir(library_dir, source_id)` is `<library>/<source_id[:12]>/`; `add_entry`/`list_entries` with temp+`os.replace` (kill-safety asserted by writing, then corrupting a leftover `.tmp`, reload OK); same-named local files → distinct entries; `validate_artifact(path)` (json parses / html non-empty / missing → False); `stage_and_commit(staging_file, final_path)` uses `os.replace` (torn-write test: staging file exists, final absent until commit).
- [ ] **Step 6.2:** Run — FAIL. Implement `engine/library.py` (≈90 lines): `source_identity(source: str) -> str`, `entry_dir`, `load_index`/`save_index` (atomic, lock), `add_entry(data_dir, entry: LibraryEntry)`, `list_entries`, `validate_artifact`, `stage_and_commit`.
- [ ] **Step 6.3:** Gates PASS. Commit: `feat(engine): managed library with atomic index and staged writes`

### Task 7: Job journal, state machine, worker

**Files:**
- Create: `src/podcast_reader/engine/jobs.py`
- Test: `tests/engine/test_jobs.py`

- [ ] **Step 7.1: Failing tests**

```python
def test_submit_returns_queued_record(store): ...
def test_transitions_persist_across_reload(tmp_path):  # submit→run fake→done; new JobStore(tmp_path) sees done + events
def test_startup_marks_running_as_interrupted(tmp_path):  # journal seeded with running job → JobStore init flips it
def test_fifo_single_worker(store):  # two jobs; second stays queued until first terminal
def test_failed_carries_structured_error(store):  # runner raising PipelineError → failed{code,message,hint}
def test_retry_by_resubmission(store):  # interrupted job's source resubmitted → new queued job
def test_awaiting_confirmation_not_reachable_via_submit(store): ...
```

Concrete API: `JobStore(data_dir, runner: Callable[[JobRecord, Callable[[PipelineEvent], None]], PipelineResult])`; `submit(source, title) -> JobRecord`; `get(job_id)`; `list()`; `start_worker()` / `shutdown()`; journal `jobs.json` atomic per transition; runner injected so tests use fakes (no real pipeline).
- [ ] **Step 7.2:** Run — FAIL. Implement `engine/jobs.py` (≈130 lines): `queue.Queue`, single `threading.Thread(daemon=True)` worker, transitions under a lock, every mutation appends events + bumps `updated_at` (use `time.time()`), journal write per transition, init pass marks `running`→`interrupted`, subscriber registry `subscribe() -> queue.Queue[PipelineEvent]` / `unsubscribe` for SSE fan-out (bounded `Queue(maxsize=256)`, drop-oldest on full).
- [ ] **Step 7.3:** Gates PASS. Commit: `feat(engine): persistent job journal with single-worker execution`

### Task 8: FastAPI app

**Files:**
- Create: `src/podcast_reader/engine/app.py`
- Test: `tests/engine/test_app.py`

- [ ] **Step 8.1: Failing tests** (fastapi TestClient; fixture builds app with tmp data_dir and fake runner):

```python
def test_missing_token_401_everywhere(client):  # parametrize all 7 routes
def test_query_token_rejected(client): ...
def test_health_returns_version_and_fingerprint(client_authed): ...
def test_post_job_then_get_state(client_authed): ...
def test_events_streams_and_heartbeats(client_authed):  # fetch stream; first bytes within timeout; ": keepalive" appears when idle (short heartbeat for test)
def test_job_record_contains_events_after_stream_missed(client_authed): ...
def test_library_and_transcript_html(client_authed, seeded_library): ...
def test_settings_get_put_roundtrip(client_authed): ...
```

- [ ] **Step 8.2:** Run — FAIL. Implement `engine/app.py` (≈140 lines): `create_app(data_dir: Path, store: JobStore, *, heartbeat_s: float = 15.0) -> FastAPI`; bearer middleware comparing `hmac.compare_digest`; routes per spec table; SSE route = sync generator over `store.subscribe()` queue with `get(timeout=heartbeat_s)` → yield `": keepalive\n\n"`, events as `data: <json>\n\n`, `finally: store.unsubscribe(q)`; `/v1/transcripts/{id}.html` via `FileResponse` after library lookup; token fingerprint = `hashlib.sha256(token.encode()).hexdigest()[:16]`.
- [ ] **Step 8.3:** Gates PASS. Commit: `feat(engine): FastAPI app with auth, jobs, SSE, library routes`

### Task 9: Process model — pre-bound socket, discovery, serve

**Files:**
- Create: `src/podcast_reader/engine/process.py`
- Modify: `src/podcast_reader/cli.py` (wire `serve_engine`), `src/podcast_reader/pipeline.py` + `src/podcast_reader/ytdlp.py`/`transcribe.py` call sites (`start_new_session=True` via a `popen_kwargs()` helper)
- Test: `tests/engine/test_process.py`

- [ ] **Step 9.1: Failing tests**

```python
def test_bind_persists_port_and_writes_discovery(tmp_path):  # bind_engine_socket → real port via getsockname; discovery file 0600 has port/pid/fingerprint/version; reuse on second call
def test_discovery_removed_on_close(tmp_path): ...
def test_sentinel_printed_after_discovery(tmp_path, capsys):  # "PODCAST_READER_READY" after file exists
def test_popen_kwargs_posix():  # start_new_session=True on POSIX; creationflags JOB-less default on win (skipif)
def test_serve_smoke(tmp_path):  # run serve_engine in a thread with port from bound socket; GET /v1/health with token → 200; shutdown cleanly
```

- [ ] **Step 9.2:** Run — FAIL. Implement `engine/process.py` (≈110 lines): `bind_engine_socket(state) -> socket` (bind persisted port; on `OSError` or port 0 → bind 0, read real port, save state); `write_discovery(path, state, sock)` atomic 0600 + `print("PODCAST_READER_READY", flush=True)`; `serve_engine(discovery_file: Path | None = None)` composing settings→state→socket→`JobStore(runner=pipeline_runner)`→`create_app`→`uvicorn.Server(Config(app)).run(sockets=[sock])` with `try/finally` discovery cleanup + `store.shutdown()`; `popen_kwargs() -> dict[str, Any]` returning `{"start_new_session": True}` on POSIX / Job-Object assignment on Windows via ctypes helper `_windows_job()` (guarded `sys.platform == "win32"`, unit-testable construction only); `pipeline_runner(record, on_event)` builds `PipelineRequest` from settings snapshot + library entry dir and calls `run_pipeline`, staging via library helpers.
- [ ] **Step 9.3:** Wire `cli.serve_engine` import; run full suite + gates — PASS.
- [ ] **Step 9.4:** Commit: `feat(engine): discovery handshake, child management, serve`

### Task 10: Docs, CI, validation

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `.github/workflows/*` (check what exists first), `openspec/changes/engine-extraction/tasks.md` (tick boxes)

- [ ] **Step 10.1:** README: add `podcast-reader serve` section + engine API sketch + env vars (`PODCAST_READER_DATA_DIR`, `PODCAST_READER_TOOLS_DIR`). CLAUDE.md: module table rows (`types.py`, `pipeline.py`, `engine/*`), test layout note.
- [ ] **Step 10.2:** CI: ensure unit job runs `tests/engine/`; add `uv run pytest tests/engine -q` only if engine tests aren't already collected (they will be — verify by running with `-q | tail`).
- [ ] **Step 10.3:** `timeout 60 openspec validate engine-extraction`; tick completed checkboxes in openspec tasks.md.
- [ ] **Step 10.4:** Full gates one more time. Commit: `docs: engine usage, module map, openspec task ticks`

### Task 11 (parallel, separate dispatch): Packaging spike

Not TDD — research deliverable per openspec tasks 5.1–5.5. Dispatch as an independent research/build agent producing `spike/SPIKE_REPORT.md` + PyInstaller spec files; evidence: Linux onedir boot + discovery handshake + CPU transcription of a 5s fixture; CUDA DLL mechanism documented; diarization sizing + merge-glue sketch + go/no-go.
