# app-packaging Specification (delta)

## ADDED Requirements

### Requirement: Release-grade frozen engine build
The repository SHALL provide a `packaging/` build that produces the production engine onedir: a PyInstaller spec with two entry points (`podcast-reader-engine` running the real `serve_engine`, `whisper-worker`) sharing one `_internal/` via MERGE/COLLECT, the custom ctranslate2 and faster-whisper hooks under version control, collection of the `podcast-reader` package metadata (`copy_metadata`) so frozen `importlib.metadata` version lookups report the real project version (per S3), and a build script that stages the yt-dlp/ffmpeg/ffprobe seeds with their generated `tools-manifest.json` into the bundle's tools directory. The output layout SHALL match the packaged-engine contract the app spawn chain and `dist.mjs --engine-dir` already expect, with no app-side changes required.

#### Scenario: Build output matches the packaged-engine contract
- **WHEN** the engine build script completes
- **THEN** the output directory contains the engine executable, the sibling `whisper-worker`, the shared `_internal/` with tool seeds and manifest, and `dist.mjs --engine-dir` consumes it unchanged

#### Scenario: Hooks under version control
- **WHEN** the engine build runs on a machine with only the repo and the documented build prerequisites
- **THEN** the ctranslate2 and faster-whisper hooks are sourced from `packaging/`, not hand-supplied

### Requirement: Frozen real-engine CI smoke
CI SHALL build the real frozen engine (replacing the spike-stub job) on an ubuntu + windows matrix and prove it end-to-end **on both legs** (per Q1): boot with a temporary data dir, complete the authenticated handshake (token from `engine-state.json`, ready sentinel, discovery file, `/v1/health`), assert the health-reported engine version equals the project version from `pyproject.toml` (per S3), install the `tiny` model pack through `POST /v1/packs/{id}/install`, transcribe a bundled 5-second fixture WAV via a submitted job on CPU, and assert the job reaches `done` with a non-empty HTML artifact. Model downloads SHALL be cached between CI runs. If demonstrated flake ever forces a downgrade, the ubuntu leg MAY drop to boot-only; the windows leg SHALL keep the full e2e (per Q1).

#### Scenario: Frozen engine transcribes in CI
- **WHEN** the frozen-smoke job runs on a pull request
- **THEN** the real frozen engine boots, reports the pyproject version on `/v1/health` (per S3), the tiny pack installs via the API, and the fixture job completes `done` with HTML

#### Scenario: Spike stub retired
- **WHEN** CI runs after this change
- **THEN** no CI job builds the spike engine

### Requirement: Diarization pack build
The repository SHALL provide a diarization pack build (`packaging/`): a dedicated CPU-torch build environment, a separate PyInstaller spec for the diarization worker, pre-seeding of the pyannote pipeline cache (fetched at build time with an HF token that has accepted the gated model terms), and a compressed pack archive with its manifest suitable for a GitHub Release pack tag. The release pipeline job publishing it SHALL be gated on the HF token secret being provisioned.

#### Scenario: Pack archive is self-contained
- **WHEN** the diarization pack build completes and the archive is installed via the pack manager
- **THEN** the worker runs offline against a fixture WAV with no HF account on the target machine

#### Scenario: Missing secret fails fast
- **WHEN** the release pack-build job runs without the HF token secret
- **THEN** it fails immediately with a message naming the missing prerequisite rather than producing a broken pack
