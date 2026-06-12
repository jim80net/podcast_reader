# engine-service Specification

## Purpose
TBD - created by archiving change engine-extraction. Update Purpose after archive.
## Requirements
### Requirement: Localhost-only HTTP service
The engine SHALL bind exclusively to `127.0.0.1` on a fixed per-install port chosen at first start and persisted in engine settings.

#### Scenario: First start picks and persists a port
- **WHEN** `podcast-reader serve` runs with no prior engine settings
- **THEN** the engine binds a free port on `127.0.0.1` and persists that port for subsequent starts

#### Scenario: Subsequent start reuses the port
- **WHEN** `podcast-reader serve` runs with persisted settings
- **THEN** the engine binds the same port as the previous run

### Requirement: Bearer-token authentication on every endpoint
The engine SHALL require `Authorization: Bearer <token>` on every endpoint, including `/v1/health`, and SHALL NOT accept the token via query parameter. The token SHALL be generated at first start and stored with owner-only permissions (0600).

#### Scenario: Missing token rejected
- **WHEN** any `/v1/*` endpoint is called without an Authorization header
- **THEN** the engine responds 401 and performs no work

#### Scenario: Token in query parameter rejected
- **WHEN** a request supplies the correct token only as a query parameter
- **THEN** the engine responds 401

#### Scenario: Valid token accepted
- **WHEN** a request supplies the correct bearer token in the Authorization header
- **THEN** the request is processed

### Requirement: Discovery file lifecycle
The engine SHALL write a discovery file (mode 0600) containing `{port, pid, token_fingerprint, version}` atomically on startup — default path `<data_dir>/engine.json`, overridable via `--discovery-file` — print a single ready sentinel line to stdout after the file is written, and remove the file on clean shutdown. The advertised port SHALL already be bound when the file is written (the engine binds the socket itself before advertising and hands it to the HTTP server).

#### Scenario: Discovery file written before sentinel
- **WHEN** the engine starts (with or without `--discovery-file <path>`)
- **THEN** the discovery file exists at the chosen path with the engine's port, PID, token fingerprint, and version before the ready sentinel is printed

#### Scenario: Advertised port is live
- **WHEN** the ready sentinel has been printed
- **THEN** a connection to the advertised port succeeds without a probe-the-port retry loop

#### Scenario: Clean shutdown removes the file
- **WHEN** the engine shuts down gracefully
- **THEN** the discovery file is removed

### Requirement: Stale-engine detection support
The engine SHALL expose `GET /v1/health` returning its version and token fingerprint so a supervisor can adopt a live engine or kill a stale process whose PID no longer answers correctly.

#### Scenario: Health probe answers
- **WHEN** `GET /v1/health` is called with a valid token
- **THEN** the engine responds 200 with `{version, token_fingerprint}`

### Requirement: Child process reaping
The engine SHALL ensure its child processes (transcription, downloads) terminate when the engine terminates: via a Job Object with kill-on-close on Windows and a dedicated process group killed on shutdown on POSIX.

#### Scenario: Engine shutdown terminates children
- **WHEN** the engine shuts down while a child subprocess is running
- **THEN** the child process is terminated as part of shutdown

### Requirement: serve subcommand
The CLI SHALL provide a `podcast-reader serve` subcommand that starts the engine, while the existing one-shot invocation shape (`podcast-reader <url-or-file> [title]`) continues to work unchanged.

#### Scenario: serve starts the engine
- **WHEN** `podcast-reader serve` is invoked
- **THEN** the engine starts and reports readiness via the sentinel

#### Scenario: One-shot CLI unchanged
- **WHEN** `podcast-reader <url> <title>` is invoked
- **THEN** the pipeline runs to completion writing artifacts to the working directory, as before

### Requirement: Graceful shutdown endpoint
The engine SHALL expose `POST /v1/shutdown` (bearer-authenticated like all routes) that responds 202 and then stops the server gracefully, running the full shutdown path: terminate child processes, stop the job worker, and remove the discovery file. This provides a portable graceful stop for supervisors on platforms without POSIX signals. `serve_engine` SHALL configure uvicorn with a bounded graceful-shutdown window (`timeout_graceful_shutdown`, 3 s) so engine exit is bounded even when a supervisor leaves an SSE stream open (per P1). `JobStore.shutdown()` SHALL set a stopping flag, and a job that fails while the store is stopping SHALL be journaled `interrupted`, not `failed` (per P2).

#### Scenario: Shutdown stops the engine cleanly
- **WHEN** `POST /v1/shutdown` is called with a valid token
- **THEN** the engine responds 202, terminates running children, and the process exits with the discovery file removed

#### Scenario: Open SSE stream cannot block shutdown (per P1)
- **WHEN** `POST /v1/shutdown` is called while a live `/v1/events` subscriber remains attached
- **THEN** the engine still exits within the bounded graceful-shutdown window and the full cleanup path runs (discovery file removed)

#### Scenario: Unauthenticated shutdown rejected
- **WHEN** `POST /v1/shutdown` is called without a valid token
- **THEN** the engine responds 401 and keeps serving

#### Scenario: Shutdown mid-job interrupts recoverably
- **WHEN** shutdown is requested while a job is running
- **THEN** the engine still exits; `JobStore.shutdown()` sets its stopping flag so a job that fails while stopping is journaled `interrupted` rather than `failed` (per P2), and any job still marked running on next start is recovered as `interrupted` per the existing recovery semantics

