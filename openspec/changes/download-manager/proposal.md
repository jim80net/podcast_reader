# Download Manager & Packs (Desktop Phase 4)

## Why

Phase 3 shipped an installable app whose packaged-engine path is an empty contract: `extraResources/engine/` exists, but no release-grade frozen engine fills it, transcription still shells out to a `whisper-ctranslate2` console script that cannot exist in a frozen bundle, and the heavyweight components the installer must not carry (CUDA DLLs ~1 GB, whisper weights up to ~3 GB, the diarization worker ~1.3 GB on disk) have no acquisition path. The Phase 1 spike (`spike/SPIKE_REPORT.md`) validated every mechanism this phase needs — dual-entry onedir, custom PyInstaller hooks, offline model-dir loading, the complete-cuDNN-9 CUDA pack, a GO verdict on the diarization worker — so Phase 4 turns that evidence into the production download manager, workers, and packaging.

## What Changes

- **Pack management in the engine**: a built-in pack registry with versioned manifests and compat ranges; `GET /v1/packs` (statuses + detected hardware + recommendations), `POST /v1/packs/{id}/install` (checksummed, resumable, atomic, manifest-written-last), `DELETE /v1/packs/{id}`; startup compat validation flags incompatible packs after an app update moves the compat range. Pack downloads run **through the engine** with progress on the existing `GET /v1/events` SSE stream (rationale in design) — on a dedicated installer thread, never the job queue.
- **Production whisper worker**: a `whisper-worker` entry point in the repo (faster-whisper in-process; argv-in / file-out / line-protocol progress on stderr, per the spike's recommended contract), frozen as the second executable of the engine onedir. The pipeline's transcribe step prefers the bundled worker (`resolve_bundled_worker`) and falls back to today's `whisper-ctranslate2` shell-out when unfrozen — CLI behavior unchanged.
- **Runtime packs**: CUDA pack (Windows+NVIDIA; pinned `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` wheels fetched from PyPI and extracted engine-side into `<data_dir>/runtime/` — the complete cuDNN 9 DLL set), whisper model packs (pinned Hugging Face snapshot files into `<data_dir>/models/<name>/`, consumed offline via `--model <dir>`), NVIDIA hardware detection driving recommendations.
- **Diarization worker pack** (spike verdict: GO): a separately frozen pyannote+torch CPU worker plus pre-seeded HF pipeline cache, hosted on GitHub Releases; engine pre-converts audio to 16 kHz mono WAV with its managed ffmpeg, the worker emits `turns.json`, and the engine performs the max-overlap speaker merge (pure stdlib) before render. `html.py` learns to render speaker labels. The parent design's cut-line still applies: if the worker freeze proves non-viable during implementation, the diarization task group detaches without blocking the rest.
- **Tools seeding + yt-dlp self-update**: bundled yt-dlp/ffmpeg/ffprobe seeds are reconciled into `<data_dir>/tools/` at engine startup (newer-wins via a versions manifest, completing the tool-resolution spec's seeding-time contract); `yt-dlp -U` runs against the user-data copy on a throttled schedule and on extraction failure (with one retry) — never against the install dir.
- **App setup UI**: a first-run setup wizard (hardware-based recommendations, live progress, resumable, skippable, re-runnable from Settings) and a Settings pack-management section (states, sizes, install/uninstall, incompatible→re-download, license attributions).
- **Packaging & CI**: `packaging/` productionizes the spike's engine.spec (real engine + whisper worker + tool seeds + custom hooks) with a build script feeding `app/scripts/dist.mjs --engine-dir`; the CI `frozen-smoke` job is upgraded from the spike stub to building the **real** engine, completing the authed discovery handshake, installing the tiny model pack through the API, and transcribing a 5-second fixture end-to-end.

No breaking changes: all engine API growth is additive; the engine version and the app's `MIN_ENGINE_VERSION` bump together (established pattern), so older engines are stale-killed-respawned per the existing app-shell contract.

## Capabilities

### New Capabilities

- `pack-management`: pack registry, manifest schema and compat validation, download/verify/resume/uninstall endpoints, SSE progress, atomic install discipline.
- `whisper-worker`: the production frozen whisper worker contract and the pipeline's freeze-aware transcribe switch (incl. CUDA runtime dir injection, model-dir resolution, device fallback).
- `runtime-packs`: CUDA pack and whisper model pack contents/sources, hardware detection and pack recommendations.
- `diarization-worker`: the frozen diarization worker contract, engine-side WAV pre-convert and speaker merge, the `diarize` setting, speaker rendering.
- `tools-seeding`: seed reconciliation into user-data tools, newer-wins versioning, yt-dlp self-update scheduling.
- `app-setup-ui`: first-run wizard, Settings pack management, the pack IPC surface.

### Modified Capabilities

- `app-packaging`: ADDED-only delta — release-grade frozen engine build (productionized spec/hooks/seeds + dist.mjs input) and the real-engine frozen CI smoke replacing the spike stub.

## Impact

- **Code**: new `src/podcast_reader/engine/packs.py` (+ registry), `src/podcast_reader/workers/` (whisper worker; diarization worker module frozen separately), `src/podcast_reader/diarize.py` (merge glue); edits in `engine/app.py` (pack routes), `engine/process.py` (seeding + validation at serve), `engine/settings.py`/`types.py` (`diarize` setting, pack/event types), `transcribe.py`/`pipeline.py` (worker switch, progress, diarize step), `html.py` (speaker labels), `ytdlp.py` (self-update-on-failure hook). New repo-root `packaging/` (spec, hooks, entry scripts, build script). App: new wizard view, Settings section, `src/shared/{types,ipc}.ts` additions, main-process pack IPC.
- **Engine API**: `GET /v1/packs`, `POST /v1/packs/{id}/install`, `DELETE /v1/packs/{id}`; pack events on `GET /v1/events`; engine version → 0.3.0, app `MIN_ENGINE_VERSION` → 0.3.0.
- **Dependencies**: new optional extra for the worker build (`faster-whisper`); PyInstaller as a build-time tool in `packaging/`; no new runtime deps for the unfrozen engine (downloads use the existing `httpx`).
- **CI**: `frozen-smoke` rebuilt around the real engine (ubuntu + windows matrix); release pipeline gains engine-build inputs (publishing still gated on signing, unchanged); diarization pack build needs an HF token secret (user-blocking task).
- **Docs**: README (packs, first-run, packaging commands), CLAUDE.md (new modules/endpoints), `app/README.md` (wizard/dev posture).
- **Out of scope**: signing/notarization (still user-blocking, Phase 3 tasks), Chrome extension (Phase 5), any pack CDN beyond GitHub Releases/PyPI/Hugging Face as hosts, hosted inference.
