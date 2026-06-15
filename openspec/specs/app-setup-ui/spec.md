# app-setup-ui Specification

## Purpose
TBD - created by archiving change download-manager. Update Purpose after archive.
## Requirements
### Requirement: First-run setup wizard
The app SHALL present a setup wizard when an app-side first-run flag is unset, the engine is ready, and recommended packs are missing: a hardware summary and the pack list from `GET /v1/packs` with recommended packs pre-selected and sizes shown, install with live progress from forwarded pack events, resume offered for `resumable` packs, and a skip action. The wizard SHALL set `whisper_device` from detected hardware (`cuda` iff Windows + NVIDIA with the CUDA pack registry-available, else `cpu`) (per S4). Completing or skipping SHALL set the flag; the wizard SHALL be re-runnable from Settings. The wizard SHALL never block the rest of the app — navigation away and back is lossless because pack state lives in the engine.

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
The Settings view SHALL gain a Packs section listing every platform-available pack with state, installed version, size, and progress; install and uninstall actions; an explicit re-download affordance for `incompatible` and `failed` packs (per S8); structured errors for `failed` packs; and the license attributions carried by installed pack manifests. Settings SHALL show an advisory when `whisper_device=cuda` with no usable CUDA pack (not installed, incompatible, or failed) (per S4/Q2).

#### Scenario: Incompatible pack offers re-download
- **WHEN** an app update leaves an installed pack flagged `incompatible`
- **THEN** the Packs section labels it and a single action re-downloads the compatible version

#### Scenario: Uninstall refusal surfaced
- **WHEN** an uninstall is refused by the engine (409, pack installing — per S1, uninstall is no longer refused for a running job)
- **THEN** the section shows the engine's reason and the pack remains installed

#### Scenario: Cuda-without-pack advisory
- **WHEN** Settings renders while `whisper_device=cuda` and no usable CUDA pack exists
- **THEN** an advisory explains that jobs will run on CPU until the CUDA pack is installed (per S4/Q2)

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

### Requirement: First-run wizard presents polished onboarding
The first-run setup wizard SHALL present a polished, welcoming onboarding: a hero with the
app mark and a welcome heading, a brief intro explaining that transcription runs locally and
the one-time component download, and clearly-labelled sections for the hardware summary and
the components to install. This is a presentation requirement layered on the existing wizard
behavior — the hardware summary, the recommended-pack pre-selection with sizes, install with
live progress, resume for resumable packs, the skip action, and the first-run-flag gate are
unchanged. All wizard content SHALL be built via the renderer's `el()`/textContent DOM
construction (no `innerHTML`).

#### Scenario: Welcoming hero on first run
- **WHEN** the setup wizard opens on first run
- **THEN** it shows the app mark, a welcome heading, an intro, and labelled hardware and
  components sections

#### Scenario: Polish does not change wizard behavior
- **WHEN** the user selects recommended packs and installs from the polished wizard
- **THEN** install proceeds with live progress and completing or skipping sets the first-run
  flag exactly as before

### Requirement: First-run chapter-provider onboarding
The first-run setup wizard SHALL present an optional chapter-provider step so a new user can enable chapter generation (and logical, idea-based paragraphs) without leaving onboarding. The step SHALL explain in plain language what an AI model provides, SHALL list the built-in providers plus the custom base-URL option (sourced from `GET /v1/providers`), SHALL let the user enter and test an API key (via the engine key-test round trip) and, on success, store it (`PUT /v1/keys`) and set it as the default provider (`PUT /v1/settings`), and SHALL reveal a base-URL field only when the custom provider is selected. The step SHALL be skippable: completing or skipping the wizard SHALL NOT require a key, and the absence of a key SHALL leave chapter generation simply disabled rather than blocking setup. The step SHALL introduce no new credential persistence — keys follow the existing in-memory-engine + vault model and SHALL NOT be written to engine disk or logs.

#### Scenario: Optional AI step is offered during setup
- **WHEN** the first-run wizard is shown
- **THEN** it presents an optional chapter-provider step explaining the benefit, with a provider selector (built-ins + custom) and a testable API-key field

#### Scenario: Setup completes without a key
- **WHEN** the user skips or finishes the wizard without entering a chapter API key
- **THEN** setup completes normally and chapter generation is simply inactive until a key is added (never a blocked wizard)

#### Scenario: Key is tested and stored
- **WHEN** the user enters a key and tests it successfully, then continues
- **THEN** the key is stored via the engine key channel and the chosen provider becomes the default, with no key material persisted to engine disk or logs

#### Scenario: Custom base URL appears only for the custom provider
- **WHEN** the user selects the custom provider
- **THEN** a base-URL field appears and is validated by the engine on save (https or http-on-localhost); other providers hide it

