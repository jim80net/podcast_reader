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

