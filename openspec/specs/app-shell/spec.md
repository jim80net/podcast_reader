# app-shell Specification

## Purpose
TBD - created by archiving change electron-app. Update Purpose after archive.
## Requirements
### Requirement: Engine supervision via the discovery handshake
The app main process SHALL locate the engine through the Phase 1 contract: resolve the engine data dir exactly as the engine does (`PODCAST_READER_DATA_DIR`, else `~/PodcastReader`), read the discovery file (`engine.json`) and the bearer token (`engine-state.json`), and adopt a running engine only when its PID is alive, `GET /v1/health` succeeds with that token, the health `token_fingerprint` matches the discovery file, and the health-reported `version` is >= `MIN_ENGINE_VERSION` — the engine version that introduces this change's endpoints; an engine reporting a newer version SHALL be adopted (per P3/Q1). Any other condition — including a version older than `MIN_ENGINE_VERSION` (per P3/Q1) — SHALL be treated as a stale engine: the app stops the stale PID (graceful shutdown when it answers, force-kill otherwise) and spawns a fresh engine. At no point SHALL two engines run for one data dir.

#### Scenario: Live engine is adopted
- **WHEN** the app starts while a healthy engine is running and discovered
- **THEN** the app adopts it without spawning a second engine

#### Scenario: Stale discovery file triggers kill-and-respawn
- **WHEN** the app starts and the discovery file names a PID that is dead, unresponsive, fails the token-fingerprint check, or reports a health `version` older than `MIN_ENGINE_VERSION` (per P3/Q1)
- **THEN** the app terminates any such process, spawns a fresh engine, and proceeds against the new discovery file

### Requirement: Engine spawn readiness via sentinel then discovery file
When spawning the engine, the app SHALL resolve the engine command in order: packaged engine executable under the app's resources (`engine/` dir), then the `PODCAST_READER_ENGINE_CMD` environment override — parsed by a documented whitespace split, so paths containing spaces are unsupported in the override; the packaged or dev postures cover those (per P6) — then the development fallback `uv run podcast-reader serve`. The app SHALL treat the engine as ready only after the `PODCAST_READER_READY` sentinel line appears on the child's stdout, then read the discovery file and verify health — it SHALL NOT poll candidate ports or parse any other stdout content.

#### Scenario: Packaged engine preferred
- **WHEN** the app runs from an installed build containing an engine resources dir
- **THEN** the packaged engine executable is spawned

#### Scenario: Dev fallback without packaged engine
- **WHEN** the app runs in development with no packaged engine and no env override
- **THEN** `uv run podcast-reader serve` is spawned and the handshake completes against it

#### Scenario: Spawn that never signals readiness fails visibly
- **WHEN** a spawned engine exits or produces no sentinel within the readiness timeout
- **THEN** the app surfaces a structured startup error (including captured stderr) instead of hanging

### Requirement: Explicit quit sequence
On quit — including the path immediately preceding `quitAndInstall` — the app SHALL first abort its own `/v1/events` stream (an open SSE response would otherwise hold graceful shutdown open, per P1), then request graceful engine shutdown via `POST /v1/shutdown`, wait for the engine process to exit within a bounded timeout, and force-kill it on timeout (relying on the engine's child-reaping for grandchildren). Adopted engines are not child processes and emit no exit event, so the app SHALL await their exit by PID polling (per P7); app quit therefore shuts down even a manually started engine — the single-ownership model is intended (per P7). The app SHALL NOT install an update while the engine process is alive.

#### Scenario: Normal quit stops the engine first
- **WHEN** the user quits the app while the engine runs
- **THEN** the app's `/v1/events` subscription is aborted before the shutdown request is sent (per P1), the engine receives the shutdown request, and the app exits only after the engine process has terminated

#### Scenario: Update applies only after engine exit
- **WHEN** a downloaded update is applied
- **THEN** the quit sequence completes (engine terminated) before `quitAndInstall` runs

#### Scenario: Hung engine cannot block quit forever
- **WHEN** the engine does not exit within the shutdown timeout
- **THEN** the app force-kills it and proceeds

### Requirement: safeStorage key vault with push-at-engine-start
The app SHALL store provider API keys only as `safeStorage`-encrypted values in its own user-data vault file, and SHALL push decrypted keys into engine memory via `PUT /v1/keys` on every engine-ready (spawn or adopt) and on every key change. Keys SHALL never be written unencrypted to disk, never sent to the renderer, and never logged. When OS-level encryption is unavailable, the app SHALL hold keys in main-process memory for the session and surface a visible warning rather than persisting plaintext.

#### Scenario: Keys repopulate after engine restart
- **WHEN** the engine restarts and the app re-completes the handshake
- **THEN** all vaulted keys are pushed to the engine before the next job needs them

#### Scenario: Vault file contains no plaintext key
- **WHEN** the vault file is inspected after keys are saved
- **THEN** no stored key value appears in plaintext

#### Scenario: Key removal clears the engine
- **WHEN** the user clears a provider key in Settings
- **THEN** the vault entry is removed and an empty value is pushed for that provider, restoring the engine's env-fallback behavior

### Requirement: podcast-reader protocol handling
The app SHALL register the `podcast-reader://` protocol (installer-level and at runtime), enforce a single app instance, and validate incoming URLs: scheme `podcast-reader`, host `transcribe`, and an `http`/`https` `url` parameter. Valid requests SHALL be submitted to the engine with `requires_confirmation` so they land in `awaiting-confirmation`, and the app SHALL focus the New view showing the URL. Invalid requests SHALL be rejected with a log entry. Protocol-initiated jobs SHALL NEVER execute without an explicit user confirmation.

#### Scenario: Protocol job awaits confirmation
- **WHEN** the OS delivers `podcast-reader://transcribe?url=https://example.com/v`
- **THEN** a job exists in `awaiting-confirmation` with that URL displayed in the New view, and no pipeline step has run

#### Scenario: Malformed protocol URL rejected
- **WHEN** a protocol URL with a wrong host, missing `url`, or non-http(s) target arrives
- **THEN** no job is created and the rejection is logged

#### Scenario: Second instance forwards and exits
- **WHEN** a protocol launch occurs while the app is already running
- **THEN** the running instance receives the URL and is focused; no second instance persists

### Requirement: Credential-free renderer
The renderer SHALL run with context isolation enabled, node integration disabled, and sandboxing on, and SHALL communicate with the main process only through the typed preload bridge and main-registered privileged URL schemes — never directly with the engine. The engine bearer token SHALL exist only in the main process; all engine HTTP/SSE traffic SHALL originate there, with events forwarded to the renderer over IPC and media bytes served through the main-mediated privileged media scheme, whose handler adds the token the renderer never holds. The renderer SHALL never possess the engine token by any path, and no renderer-originated request SHALL ever carry the token to the engine.

#### Scenario: Token absent from renderer
- **WHEN** the renderer context is inspected during e2e tests
- **THEN** the bearer token is not reachable from any renderer-accessible API or global

#### Scenario: Renderer reaches the engine only through main
- **WHEN** any view needs engine data or media
- **THEN** the request flows either through the preload bridge to the main-process engine client, or through a main-registered privileged scheme whose handler runs in the main process — never as a direct renderer-to-engine connection bearing the token

### Requirement: Progress stream with record hydration
The main process SHALL consume `GET /v1/events` using header-authenticated streaming, reconnect with backoff on stream loss, and re-hydrate state from the job records (`GET /v1/jobs`) after every (re)connect, so the renderer's view of job state never depends on an unbroken stream. After an engine respawn the app SHALL establish a fresh stream for the new engine and SHALL abort the prior engine's stream so exactly one stream is active; the re-hydrate-after-(re)connect behavior SHALL recover any job state (including journal-recovered interrupted jobs) produced across the respawn.

#### Scenario: Missed events recovered after reconnect
- **WHEN** the events stream drops and reconnects (including across a respawn)
- **THEN** the app re-hydrates job records from `GET /v1/jobs` so no job state is lost, with exactly one active stream

### Requirement: Privileged media protocol
The app SHALL register an internal privileged scheme (distinct from the external `podcast-reader://` deep-link scheme) as standard, secure, and stream-capable **before** the app's ready event, and SHALL install its handler at ready. The handler for `media` requests SHALL validate the `source_id` against the sha256-hexdigest pattern (`^[0-9a-f]{64}$`) and reject anything else, SHALL only ever target the loopback engine (no arbitrary URL, no SSRF), SHALL add the engine bearer token (which the renderer never holds), SHALL forward the inbound `Range` header, and SHALL return the engine response verbatim so the partial-content status and headers reach the media element and the body streams without buffering the whole file in the main process. Media bytes SHALL NOT pass through IPC.

#### Scenario: Media element loads via the privileged scheme
- **WHEN** the renderer sets a media element's source to the internal media scheme for a valid `source_id`
- **THEN** the main-process handler proxies the engine route with the bearer token and streams the bytes, and the renderer never sees the token

#### Scenario: Malformed media id is rejected
- **WHEN** a media request arrives with a `source_id` that is not a 64-character sha256 hex string
- **THEN** the handler rejects it without contacting the engine

#### Scenario: Seeking is preserved end to end
- **WHEN** a media element issues a ranged request through the scheme
- **THEN** the handler forwards the `Range` header and returns the engine's `206` response so seeking works

### Requirement: Media info IPC
The preload bridge SHALL expose a typed `mediaInfo(sourceId)` call that the main process answers from the engine's `GET /v1/media/{id}/info`, so the renderer can choose the player kind and observe preparation status without holding the engine token. Only this metadata SHALL cross IPC; media bytes SHALL reach the renderer solely through the privileged media scheme.

#### Scenario: Renderer learns the player kind over IPC
- **WHEN** the renderer calls `window.api.mediaInfo(sourceId)`
- **THEN** the call flows through the preload bridge to the main-process engine client and returns the media info (kind, status, duration)

### Requirement: Engine respawn supervision
When a **spawned** engine process exits unexpectedly (not as part of the quit sequence), the app SHALL attempt to respawn it automatically rather than only surfacing a failed status, so a crashed engine recovers without an app restart. Detection SHALL use the child-process exit event only; adopted engines (which emit no exit event) SHALL retain the prior behavior. Respawn SHALL be bounded by a policy: it SHALL back off between attempts and SHALL give up to a terminal `failed` status after a fixed number of consecutive failed attempts; the consecutive-failure count SHALL reset after the engine has run healthy for a configured duration. On each respawn the app SHALL reconstruct the same live state it builds at start — re-push the vaulted keys, re-establish the events stream (aborting the dead engine's stream first so it cannot re-attach to the new engine on the stable port and token), and re-arm exit detection. While respawning, the app SHALL report a `restarting` engine status; once the engine reaches ready it SHALL report `ready`. A failed `ensure()`/spawn during respawn SHALL count as a failure against the policy. The respawn path SHALL NOT run during or after the quit sequence: `quit()` SHALL signal a quitting state that the respawn routine re-checks after each asynchronous step, and a child spawned after a quit has begun SHALL be force-killed rather than wired up. During the restart window the engine SHALL be reported not-ready to the privileged media path (no proxying to a dead engine).

#### Scenario: Spawned engine crash is respawned
- **WHEN** a spawned engine exits unexpectedly while the app owns it and is not quitting
- **THEN** the app reports `restarting`, respawns the engine after a backoff, re-pushes the vaulted keys, re-establishes the events stream, and returns to `ready`

#### Scenario: Repeated crashes give up
- **WHEN** an engine crashes and every respawn attempt also fails up to the configured limit
- **THEN** the app stops retrying and reports a terminal `failed` status

#### Scenario: Healthy run resets the failure budget
- **WHEN** an engine respawns, runs healthy past the reset duration, and later crashes
- **THEN** that later crash starts a fresh respawn budget rather than counting against the earlier burst

#### Scenario: Quit during respawn never leaves an engine running
- **WHEN** the quit sequence begins while a respawn is in flight (during its backoff or while spawning)
- **THEN** no engine is left running afterward — a child spawned after quit began is force-killed — and the app does not wire it up

#### Scenario: Crash during quit is not respawned
- **WHEN** the engine exits as part of the quit sequence
- **THEN** the app does not treat it as an unexpected crash and does not respawn

#### Scenario: Manual restart from failed
- **WHEN** the engine is in the terminal `failed` state and the user invokes restart
- **THEN** the app resets the respawn budget and spawns a fresh engine without going through the quit sequence, and concurrent restart invocations do not spawn more than one engine

