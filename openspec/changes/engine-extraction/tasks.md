# Engine Extraction — Tasks

## 1. Boundary types & shared pipeline

- [ ] 1.1 Define typed boundaries in `src/podcast_reader/types.py`: `PipelineRequest`, `PipelineEvent`, `StepName`, `JobRecord`, `LibraryEntry`, `EngineSettings` (TypedDicts/dataclasses; mypy strict)
- [ ] 1.2 Extract `src/podcast_reader/pipeline.py` from `cli._run_pipeline`: step functions (resolve, captions, download, transcribe, chapters, render) + `run_pipeline(request, on_event)`; unit tests with mocked subprocess per existing convention
- [ ] 1.3 Chapters fault isolation in the shared step (catch → warning event → chapterless render); tests for engine-path and CLI-path behavior (CLI exits 0, HTML written)
- [ ] 1.4 Rewire `cli.py` one-shot mode onto `run_pipeline` with a print-adapter; port `tests/test_cli.py` patch targets and the `_run_pipeline` import to `podcast_reader.pipeline`, preserving every assertion (behavior parity is the acceptance bar)

## 2. Transcript library

- [ ] 2.1 `engine/library.py`: source-identity hashing (URL / file content), artifact dirs, atomic `library.json` writes; unit tests incl. crash-during-write (temp file present, index intact)
- [ ] 2.2 Cache re-validation on hit (JSON parses, HTML non-empty; invalid → delete + regenerate); staged artifact writes (staging dir + atomic move on step completion); corrupt-cache and torn-write tests
- [ ] 2.3 Same-stem local file collision test; same-URL reuse test

## 3. Engine service

- [ ] 3.1 `engine/settings.py`: data dir resolution (`~/PodcastReader/` default, env override), engine-owned state file (port, token; 0600) separate from PUT-able user settings; both atomic under a lock; `EngineSettings` fields enumerated; settings snapshot at job dequeue
- [ ] 3.2 `engine/app.py`: FastAPI app + bearer middleware (header-only; 401 on query token); endpoints `/v1/health`, `/v1/settings`
- [ ] 3.3 `engine/jobs.py`: persistent job journal (`jobs.json`, atomic write per transition), FIFO single-worker thread, state machine (queued/awaiting-confirmation/running/done/failed/interrupted), startup interrupted-marking, retry-by-resubmission semantics; unit tests for transitions, persistence across restart, and retry
- [ ] 3.4 Endpoints `/v1/jobs` (POST/GET), `/v1/events` (SSE: per-client bounded queues, `get(timeout)` + keepalive comment heartbeat, cleanup in finally), `/v1/library`, `/v1/transcripts/{id}.html`; TestClient tests incl. auth matrix, events-vs-record consistency, and disconnect cleanup
- [ ] 3.5 `engine/process.py`: pre-bound socket handshake (bind → persist port → write discovery file 0600 atomically → sentinel → `uvicorn.Server.serve(sockets=...)`), default discovery path + `--discovery-file` override, removal on shutdown; Windows Job Object (ctypes) / POSIX process-group child management; lifecycle tests (POSIX path in CI)
- [ ] 3.6 `serve` subcommand in `cli.py` (argparse subparsers; one-shot shape preserved); test both invocation shapes

## 4. Tool resolution

- [ ] 4.1 Rewrite `tools.resolve_tool` with precedence `tools_dir` param (default `PODCAST_READER_TOOLS_DIR` env) → frozen bundle dir / interpreter sibling → PATH name; `sys.frozen` handling; update existing tests + add the four spec scenarios
- [ ] 4.2 Reconcile 4.1 against spike evidence from 5.2 (frozen-bundle tool layout); adjust implementation if the spike contradicts the assumed layout

## 5. Packaging spike (report + prototype, not shipped)

- [ ] 5.1 `spike/engine.spec` + build script: PyInstaller onedir of engine with faster-whisper/ctranslate2; second entry point `whisper-worker`; build on Linux, document Windows build steps
- [ ] 5.2 Validate frozen prototype: discovery handshake + CPU transcription of 5-second fixture WAV end-to-end; record output-shape parity vs whisper-ctranslate2 fixture
- [ ] 5.3 CUDA DLL injection mechanism (runtime dir on DLL search path) documented with evidence; ctranslate2↔cuDNN pin matrix recorded
- [ ] 5.4 Diarization sizing: frozen pyannote+torch worker size, merge-glue interface sketch (whisper segments + speaker turns), torchcodec ffmpeg pathing; **go/no-go recommendation** against the cut-line
- [ ] 5.5 `spike/SPIKE_REPORT.md` consolidating 5.1–5.4 + measured installer-relevant sizes

## 6. Docs, quality gates, integration

- [ ] 6.1 README + CLAUDE.md: engine/`serve` docs, new module table rows, dependency changes (fastapi/uvicorn core, pyinstaller dev)
- [ ] 6.2 Full gates: pytest (unit), mypy strict, ruff check + format; fix all findings
- [ ] 6.3 CI: engine-API test job; frozen smoke test wired for the spike prototype (Linux)
- [ ] 6.4 `openspec validate engine-extraction` passes; systems-review of implementation diff; PR
