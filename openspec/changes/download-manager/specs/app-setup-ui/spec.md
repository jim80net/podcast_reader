# app-setup-ui Specification (delta)

## ADDED Requirements

### Requirement: First-run setup wizard
The app SHALL present a setup wizard when an app-side first-run flag is unset, the engine is ready, and recommended packs are missing: a hardware summary and the pack list from `GET /v1/packs` with recommended packs pre-selected and sizes shown, install with live progress from forwarded pack events, resume offered for `resumable` packs, and a skip action. Completing or skipping SHALL set the flag; the wizard SHALL be re-runnable from Settings. The wizard SHALL never block the rest of the app — navigation away and back is lossless because pack state lives in the engine.

#### Scenario: Recommended packs pre-selected
- **WHEN** the wizard opens on a Windows machine with an NVIDIA GPU
- **THEN** the CUDA pack and the GPU-appropriate model pack are pre-checked with their download sizes

#### Scenario: Progress survives navigation
- **WHEN** the user starts installs, navigates to another view, and returns
- **THEN** the wizard (or Settings) shows current install progress hydrated from `GET /v1/packs`

#### Scenario: Skip is honored and reversible
- **WHEN** the user skips the wizard
- **THEN** it does not reappear on next launch, and Settings offers "Run setup again"

#### Scenario: Interrupted install resumable on next run
- **WHEN** the app restarts while a pack was partially downloaded
- **THEN** the pack is shown resumable and one action continues the download

### Requirement: Settings pack management section
The Settings view SHALL gain a Packs section listing every platform-available pack with state, installed version, size, and progress; install and uninstall actions; an explicit re-download affordance for `incompatible` packs; structured errors for `failed` packs; and the license attributions carried by installed pack manifests.

#### Scenario: Incompatible pack offers re-download
- **WHEN** an app update leaves an installed pack flagged `incompatible`
- **THEN** the Packs section labels it and a single action re-downloads the compatible version

#### Scenario: Uninstall refusal surfaced
- **WHEN** an uninstall is refused by the engine (409, job running)
- **THEN** the section shows the engine's reason and the pack remains installed

#### Scenario: Attribution visible
- **WHEN** packs carrying license notices are installed
- **THEN** their attribution texts are reachable from the Packs section

### Requirement: Pack IPC surface
The preload bridge SHALL expose typed pack operations (`listPacks`, `installPack`, `uninstallPack`) following the established credential-free pattern — all engine traffic main-side — and pack events SHALL be forwarded to the renderer over the existing engine-event push channel, with the new shapes mirrored in `src/shared/types.ts` and covered by the integration smoke's key-set parity assertion.

#### Scenario: Renderer stays credential-free
- **WHEN** the renderer drives a pack install
- **THEN** the request crosses the IPC bridge and the bearer token never reaches the renderer

#### Scenario: Type mirrors verified against the real engine
- **WHEN** the real-engine integration smoke runs
- **THEN** the pack status payload's key set matches the TypeScript mirror exactly
