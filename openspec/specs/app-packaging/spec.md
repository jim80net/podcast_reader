# app-packaging Specification

## Purpose
TBD - created by archiving change electron-app. Update Purpose after archive.
## Requirements
### Requirement: Installer targets
The app SHALL build with electron-builder into an NSIS per-user installer (Windows) and a dmg plus auto-update zip (macOS), both registering the `podcast-reader://` protocol at install time. Unsigned local builds of both targets SHALL be producible and functional for development.

#### Scenario: Unsigned dev installer works end-to-end
- **WHEN** an unsigned build is installed on a dev machine with an engine available (packaged dir or dev posture)
- **THEN** the app launches, completes the engine handshake, and runs a job

#### Scenario: Protocol registered by the installer
- **WHEN** the app is installed and the OS opens a `podcast-reader://` URL
- **THEN** the installed app receives it

### Requirement: Engine payload as extraResources
The frozen engine onedir SHALL ship outside the asar archive, as `extraResources` under `<resources>/engine/`, preserving the spike layout (engine executable, sibling `whisper-worker`, shared `_internal/`). The build SHALL accept an engine-dir input and SHALL also produce a valid app without one (development builds fall back to the app-shell spawn chain). This layout is the contract Phase 4 fills with the release-grade engine.

#### Scenario: Packaged engine is executable in place
- **WHEN** a build is given a frozen engine dir
- **THEN** the installed app spawns `<resources>/engine/<engine executable>` successfully

#### Scenario: Engine-less dev build still launches
- **WHEN** a build is produced without an engine dir
- **THEN** the app starts and uses the env-override or `uv run` fallback

### Requirement: Auto-update with full-download strategy
The app SHALL auto-update via electron-updater against GitHub Releases using full-download updates (differential/blockmap optimization explicitly deferred until shell and engine release cadences decouple — the extraResources layout keeps that path open). Updates SHALL download in the background, apply only after user consent, and SHALL be installed only after the app-shell quit sequence has terminated the engine.

#### Scenario: Update never replaces files under a running engine
- **WHEN** the user accepts an update
- **THEN** the engine is shut down and exited before installation begins

#### Scenario: Declined update defers
- **WHEN** the user declines an available update
- **THEN** the app continues on the current version and re-offers later

### Requirement: Signing and notarization gates
Code signing (Windows) and signing+notarization (macOS) SHALL be wired as explicit prerequisite-gated steps: release installer builds in CI (tag pipelines on Windows/macOS runners) SHALL be enabled only once signing credentials are provisioned, and macOS auto-update verification is acknowledged to require a signed build. Until then, releases are dev-channel unsigned artifacts with documented open-anyway caveats.

#### Scenario: Tag pipeline blocked without credentials
- **WHEN** a release tag is pushed before credentials exist
- **THEN** no unsigned artifact is published as a release installer by CI

#### Scenario: Credentialed pipeline signs and notarizes
- **WHEN** credentials are provisioned and a tag builds
- **THEN** the Windows installer is signed and the macOS artifact is signed and notarized before publishing

### Requirement: App test suites in CI
CI SHALL gain a node job running the app's typecheck, unit tests, and Playwright e2e suite against the mock engine on every PR, plus an integration-marked smoke test that spawns the real engine via the development posture and completes the discovery handshake.

#### Scenario: PR CI exercises the app
- **WHEN** a pull request touches the repo
- **THEN** the node job runs typecheck, unit, and mock-engine e2e to completion

#### Scenario: Real engine handshake proven in CI
- **WHEN** the smoke test runs
- **THEN** a real `podcast-reader serve` process is spawned, the sentinel and discovery file are observed, and an authenticated health check succeeds

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

### Requirement: Branded app icon across surfaces
The app SHALL ship a branded icon — the "play + transcript lines" mark — across the
installer, the macOS dock/.app, the Windows taskbar, and the runtime window, derived from a
single committed source. The icon source SHALL be committed as a 1024×1024 SVG
(`build/icon.svg`) and a rendered 1024×1024 PNG (`build/icon.png`); a documented dev script
(`scripts/build-icons.mjs`, `npm run build-icons`) SHALL render the SVG to the PNG and assert
the PNG is a valid 1024×1024 image. electron-builder SHALL derive the platform `.icns`
(macOS) and `.ico` (Windows/NSIS) from `build/icon.png` at packaging time; the build
SHALL NOT require committing or hand-generating `.icns`/`.ico`, and a fresh checkout and CI
SHALL build the installer without `rsvg-convert` or ImageMagick. The signing/notarization
configuration SHALL be unaffected.

#### Scenario: Single committed source derives the platform icons
- **WHEN** the app is packaged with electron-builder from a fresh checkout
- **THEN** the installer and the bundled app carry the branded icon, derived from the
  committed `build/icon.png`, with no `.icns`/`.ico` committed and no rsvg/ImageMagick on the
  build host

#### Scenario: The runtime window shows the mark in dev and packaged runs
- **WHEN** the app window is created
- **THEN** the window is given the branded icon, resolved from the packaged
  `<resources>/icon.png` when packaged and from `<app>/build/icon.png` in dev

#### Scenario: Re-rendering the source reproduces the committed PNG
- **WHEN** `npm run build-icons` is run against the committed `build/icon.svg`
- **THEN** it produces a valid 1024×1024 PNG at `build/icon.png` and fails loudly if the
  output is not a 1024×1024 PNG

