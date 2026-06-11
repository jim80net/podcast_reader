# Engine Extraction + Packaging Spike (Desktop Phase 1)

## Why

The desktop packaging design (docs/superpowers/specs/2026-06-11-desktop-packaging-design.md, v3) turns podcast_reader into a desktop product with three faces — Electron app, Chrome extension, CLI — sharing one engine. Phase 1 creates that engine: today the pipeline is a print-as-you-go CLI function (`cli._run_pipeline`) with no API, no job state, no progress events, and outputs as loose files in cwd. Every later phase depends on this seam, and the engine's internal shape (how transcription is invoked, where tools resolve) depends on the PyInstaller freezing spike, so both happen first and together.

## What Changes

- New `podcast_reader.engine` package: FastAPI app on `127.0.0.1` with bearer-token auth — `POST /v1/jobs`, `GET /v1/jobs/{id}`, `GET /v1/events` (SSE), `GET /v1/library`, `GET /v1/transcripts/{id}.html`, `GET/PUT /v1/settings`, `GET /v1/health`.
- `_run_pipeline` refactored into a step-based job runner (resolve → captions|download → transcribe → chapters → render) emitting progress events; orchestration shared by CLI and engine.
- Job lifecycle states: queued / awaiting-confirmation / running(step) / done / interrupted / failed{code, message, hint}; jobs found `running` at engine startup become `interrupted`.
- Chapters step becomes fault-isolated in BOTH engine and CLI: provider errors degrade to a chapterless transcript with a structured warning. (Current CLI crashes before writing HTML — `cli.py` has no handling around `generate_chapters`.)
- Managed library: `~/PodcastReader/` (configurable) with `library.json` index; engine is sole index writer (temp + atomic rename); entries keyed by source identity (URL / content hash), not filename stem; cache hits re-validate artifacts (corrupt = miss). CLI one-shot mode keeps writing loose files to cwd unchanged.
- Engine process model: fixed per-install port persisted in a 0600 discovery file (`{port, pid, token_fingerprint, version}`), ready sentinel on stdout, children owned via Windows Job Object / POSIX process group, adopt-or-kill of stale engines via `/v1/health` probe.
- New CLI subcommand `podcast-reader serve`.
- `resolve_tool` becomes freeze-aware: under `sys.frozen`, resolves against the bundle tools dir and `<userData>/tools/`, never `Path(sys.executable).parent`.
- **Packaging spike (deliverable: report + working prototype, on Phase 1's critical path):** PyInstaller onedir build of the engine with faster-whisper/ctranslate2 bundled and a whisper worker as a second entry point; CUDA DLL pack loading from a runtime dir; diarization worker viability + merge-glue interface (whisper-ctranslate2's speaker merge is lost when we move off it) + torchcodec→ffmpeg pathing; go/no-go on the diarization cut-line; installer size measurement.

No breaking changes to the existing CLI surface.

## Capabilities

### New Capabilities

- `engine-service`: localhost FastAPI service — endpoints, bearer-token auth (header-only), discovery file, fixed-port process model, child reaping, adopt-or-kill, `serve` subcommand.
- `job-pipeline`: job model and step runner — states, progress events, chapters fault isolation (engine + CLI), interruption semantics.
- `transcript-library`: managed storage — library dir, atomic index writes, source-identity keys, artifact cache re-validation.
- `tool-resolution`: freeze-aware external-tool resolution (extends current `resolve_tool` behavior with frozen-bundle and user-data precedence).

### Modified Capabilities

None (no existing specs in `openspec/specs/`).

## Impact

- **Code:** new `src/podcast_reader/engine/`; refactor of `cli.py` (pipeline moves out, `serve` added, chapters fault isolation); `tools.py` rewrite; new deps `fastapi`, `uvicorn` (core), `pyinstaller` (dev).
- **Tests:** existing convention carries over (subprocess mocked, equality matchers); new TestClient API tests, job-model unit tests, cache-corruption tests, freeze-aware resolve_tool tests. Frozen-artifact smoke test lands in CI for the spike prototype (Linux build smoke in PR CI; Windows/macOS in tag pipelines later phases).
- **Docs:** README + CLAUDE.md gain `serve` and engine docs.
- **Out of scope (later phases):** Electron app, extension, download manager, multi-provider chapters, signing.
