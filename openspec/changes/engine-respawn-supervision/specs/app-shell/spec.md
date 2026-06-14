# app-shell Specification (delta)

## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Progress stream with record hydration
The main process SHALL consume `GET /v1/events` using header-authenticated streaming, reconnect with backoff on stream loss, and re-hydrate state from the job records (`GET /v1/jobs`) after every (re)connect, so the renderer's view of job state never depends on an unbroken stream. After an engine respawn the app SHALL establish a fresh stream for the new engine and SHALL abort the prior engine's stream so exactly one stream is active; the re-hydrate-after-(re)connect behavior SHALL recover any job state (including journal-recovered interrupted jobs) produced across the respawn.

#### Scenario: Missed events recovered after reconnect
- **WHEN** the events stream drops and reconnects (including across a respawn)
- **THEN** the app re-hydrates job records from `GET /v1/jobs` so no job state is lost, with exactly one active stream
