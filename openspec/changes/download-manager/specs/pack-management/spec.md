# pack-management Specification (delta)

## ADDED Requirements

### Requirement: Built-in pack registry
The engine SHALL ship a static pack registry defining every known pack: id, kind (`runtime` | `model` | `worker`), display name, platform gate, download specification (exact URLs with per-file sha256 and size), component versions, the compat range this engine build requires, and license notices. The registry SHALL be data plus pure functions, evaluable without network access.

#### Scenario: Registry lists all packs
- **WHEN** the registry is enumerated
- **THEN** it contains the CUDA runtime pack, the whisper model packs (including `tiny`), and the diarization worker pack, each with pinned URLs and sha256 digests

#### Scenario: Platform-gated pack excluded
- **WHEN** the registry is evaluated on a platform a pack does not support (e.g. the CUDA pack on macOS)
- **THEN** that pack is reported unavailable for the platform rather than installable

### Requirement: Pack status endpoint
The engine SHALL expose `GET /v1/packs` (bearer-authenticated like all routes) returning detected hardware (`platform`, `nvidia_gpu`, `gpu_names`) and, for every registry pack available on the platform: id, display name, kind, size, state (`not-installed` | `resumable` | `installing` | `installed` | `incompatible` | `failed`), installed version where applicable, a `recommended` boolean computed from detected hardware, progress when installing, and a structured error when failed. Pack state SHALL be derived from disk (installed manifest, staging partials) plus in-memory installer state — no separate journal.

#### Scenario: Fresh install shows recommendations
- **WHEN** `GET /v1/packs` is called on a Windows machine with an NVIDIA GPU and no packs installed
- **THEN** the response reports `nvidia_gpu: true`, the CUDA pack and a GPU-appropriate model pack as `recommended`, and all packs `not-installed`

#### Scenario: Partial download surfaces as resumable
- **WHEN** the engine restarts while a pack download was mid-flight
- **THEN** `GET /v1/packs` reports that pack `resumable`, not `installing` and not `not-installed`

### Requirement: Pack installation endpoint
The engine SHALL expose `POST /v1/packs/{id}/install` returning 202 and running the install asynchronously on a dedicated installer thread — never on the job-store worker — FIFO across packs with one transfer at a time. Installing an unknown pack id SHALL return 404; re-POSTing an installing or installed pack SHALL be idempotent (202, no duplicate work). Downloads SHALL resume partial files via HTTP Range when staging partials exist.

#### Scenario: Install does not block transcription jobs
- **WHEN** a pack install is in progress and a transcription job is submitted
- **THEN** the job executes on the job worker without waiting for the download

#### Scenario: Interrupted download resumes
- **WHEN** an install is interrupted (engine restart or network failure) and `POST /v1/packs/{id}/install` is called again
- **THEN** the download continues from the partial file's byte offset rather than restarting from zero

#### Scenario: Duplicate install request is idempotent
- **WHEN** `POST /v1/packs/{id}/install` is called while that pack is already installing
- **THEN** the engine responds 202 and no second download starts

### Requirement: Checksum verification and atomic install
Every downloaded file SHALL be verified against the registry's sha256 before installation; verification failure SHALL discard the file and mark the pack `failed` with a structured error — corrupt content is never installed. Installation SHALL be atomic-by-construction: files are staged, and the pack's `pack-manifest.json` (`{pack_schema, id, version, component_versions, files, licenses}`) is written last; a pack directory without a valid manifest SHALL be treated as not installed.

#### Scenario: Hash mismatch fails closed
- **WHEN** a downloaded file's sha256 does not match the registry pin
- **THEN** the file is deleted, the pack state becomes `failed` with an error naming the verification failure, and no manifest is written

#### Scenario: Crash mid-install leaves no phantom pack
- **WHEN** the engine dies after files are staged but before the manifest is written
- **THEN** on restart the pack is not reported `installed`

### Requirement: Startup compatibility validation
At startup the engine SHALL validate every installed pack manifest against the registry's compat range (pack schema version and component pairings, e.g. ctranslate2 ↔ cuDNN). Incompatible packs SHALL be flagged `incompatible`, treated as absent by the pipeline, and left on disk for the re-download flow to replace.

#### Scenario: App update moves the compat range
- **WHEN** the engine starts with an installed pack whose manifest falls outside the new registry's compat range
- **THEN** `GET /v1/packs` reports it `incompatible` and jobs behave as if it were not installed

#### Scenario: Compatible packs pass silently
- **WHEN** the engine starts with installed packs inside the compat range
- **THEN** they are reported `installed` and used by the pipeline

### Requirement: Pack uninstall endpoint
The engine SHALL expose `DELETE /v1/packs/{id}` removing the pack's directory and manifest. Uninstall SHALL be refused with 409 while a job is running or while that pack is installing; unknown ids SHALL return 404.

#### Scenario: Uninstall removes the pack
- **WHEN** `DELETE /v1/packs/{id}` succeeds
- **THEN** the pack's files and manifest are gone and `GET /v1/packs` reports it `not-installed`

#### Scenario: Uninstall refused during a running job
- **WHEN** `DELETE /v1/packs/{id}` is called while a job is running
- **THEN** the engine responds 409 and removes nothing

### Requirement: Pack progress on the event stream
Pack installer progress (state transitions and download progress with bytes/total) SHALL be published as self-describing events on the existing `GET /v1/events` SSE stream, distinguishable from job events by kind. `GET /v1/packs` SHALL remain the source of truth for clients that missed events (the job-record hydration pattern).

#### Scenario: Progress observable live
- **WHEN** a client subscribes to `GET /v1/events` during a pack install
- **THEN** it receives pack progress events for that install as bytes arrive

#### Scenario: Job event consumers unaffected
- **WHEN** pack events and job events interleave on the stream
- **THEN** each event's kind identifies it, and existing job-event consumers ignore pack events without error
