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
The engine SHALL require `Authorization: Bearer <token>` on every endpoint, including `/v1/health`, and SHALL NOT accept the token via query parameter. The token SHALL be generated at first start and stored with owner-only permissions (0600). The single exception SHALL be `POST /v1/pair/claim` — the auth-middleware exemption SHALL match on method and exact path, so any other method on `/v1/pair/claim` still requires the bearer token (per U5) — which exists precisely to issue the token to a not-yet-authenticated extension and is protected by the pairing-code exchange requirement instead.

#### Scenario: Missing token rejected
- **WHEN** any `/v1/*` endpoint other than `POST /v1/pair/claim` is called without an Authorization header
- **THEN** the engine responds 401 and performs no work

#### Scenario: Token in query parameter rejected
- **WHEN** a request supplies the correct token only as a query parameter
- **THEN** the engine responds 401

#### Scenario: Valid token accepted
- **WHEN** a request supplies the correct bearer token in the Authorization header
- **THEN** the request is processed

#### Scenario: Claim is reachable without credentials
- **WHEN** `POST /v1/pair/claim` is called without an Authorization header
- **THEN** the request is not rejected by the auth middleware and is evaluated against the pairing-code exchange rules

#### Scenario: Non-POST methods on the claim path stay authenticated (per U5)
- **WHEN** `/v1/pair/claim` is called with any method other than POST and no Authorization header
- **THEN** the engine responds 401 — the exemption matches (method, path), not the path alone

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

### Requirement: Pairing-code exchange
The engine SHALL support a user-mediated pairing exchange for token-less clients. `POST /v1/pair` (bearer-authed) SHALL generate a 6-character single-use code from an unambiguous alphabet, hold it exclusively in process memory with a 300-second expiry and a 5-failed-attempt budget, and return `{code, expires_at}`; minting a new code SHALL invalidate any previous one. `POST /v1/pair/claim` (unauthenticated) SHALL accept `{code}` and, on a constant-time match against an unexpired, unexhausted code, respond with `{token}` exactly once and invalidate the code. Wrong, expired, exhausted, or absent codes SHALL all produce a uniform 403 with a self-authored detail that does not distinguish the cases. To keep in-browser attackers from burning the attempt budget during the pairing window (per U3), claim SHALL reject requests whose `Content-Type` is not `application/json` and requests bearing an `Origin` header with an `http` or `https` scheme (a `chrome-extension://` origin SHALL NOT be rejected); these gate rejections SHALL NOT count against the attempt budget. Rationale: requiring JSON makes a page-initiated request non-simple — it triggers a CORS preflight the engine never approves, so it never arrives — and the Origin rejection backstops simple requests; the extension sends real JSON, is CORS-exempt via its host permission, and bears a `chrome-extension://` origin. Pairing codes SHALL never be written to any file or log.

#### Scenario: Mint requires the bearer token
- **WHEN** `POST /v1/pair` is called without a valid token
- **THEN** the engine responds 401 and no code is created

#### Scenario: Valid claim returns the token once
- **WHEN** a code is minted and `POST /v1/pair/claim` supplies it before expiry
- **THEN** the response contains the engine bearer token, and a second claim with the same code responds 403

#### Scenario: Expired code rejected uniformly
- **WHEN** a claim supplies a code after its 300-second expiry
- **THEN** the engine responds 403 with the same shape as a wrong-code rejection

#### Scenario: Attempt budget invalidates the code
- **WHEN** five claims supply wrong codes while a code is pending
- **THEN** the pending code is invalidated and a subsequent claim with the correct code responds 403

#### Scenario: New mint replaces the old code
- **WHEN** `POST /v1/pair` is called while an unclaimed code is pending
- **THEN** only the newly returned code can be claimed

#### Scenario: Page-origin claim rejected without burning the budget (per U3)
- **WHEN** a claim arrives with an `https`-scheme `Origin` header or a non-`application/json` content type while a code is pending
- **THEN** the request is rejected, the pending code's attempt budget is unchanged, and a subsequent valid claim still succeeds

#### Scenario: Codes never persisted
- **WHEN** engine files (journal, settings, discovery, logs) are inspected after minting and claiming
- **THEN** no pairing code appears in any of them

### Requirement: Media info endpoint
The engine SHALL expose `GET /v1/media/{source_id}/info` (bearer-authenticated like all routes) returning `{kind, youtube_id, duration_s, status, progress}` where `kind ∈ {youtube, video, audio, unavailable}` and `status ∈ {ready, preparing, unavailable}`. Classification and probing SHALL live in the engine so no client parses source URLs. Probing SHALL NOT depend on `ffprobe` (which is not guaranteed in the frozen bundle): duration and the presence of a video track SHALL be determined via `ffmpeg` or from the yt-dlp format for remote sources. For an uncached remote source the endpoint SHALL report `status: preparing` and initiate the single-flight acquisition; for YouTube it SHALL return immediately with the extracted id.

#### Scenario: Info reports kind for a local video
- **WHEN** `GET /v1/media/{source_id}/info` is called for a local entry with a video track
- **THEN** it returns `kind: video`, a duration, and `status: ready`

#### Scenario: Info kicks off lazy preparation
- **WHEN** `GET /v1/media/{source_id}/info` is called for an uncached remote source
- **THEN** it returns `status: preparing` and starts the download, without blocking on completion

#### Scenario: YouTube info is immediate
- **WHEN** `GET /v1/media/{source_id}/info` is called for a YouTube source
- **THEN** it returns `kind: youtube` with `youtube_id` and does not download any media

### Requirement: Media byte-serving endpoint with Range
The engine SHALL expose `GET /v1/media/{source_id}` (bearer-authenticated) serving the cached or local media bytes with HTTP Range support — honoring the `Range` request header with `206 Partial Content` and `Content-Range`/`Accept-Ranges` so the player can seek — implemented with a Range-capable file response rather than a non-Range streaming response. A request for media that does not exist or cannot be produced SHALL return `404`.

#### Scenario: Range request returns partial content
- **WHEN** `GET /v1/media/{source_id}` is called with a `Range` header for a ready media file
- **THEN** the engine responds `206` with the requested byte range and `Content-Range`

#### Scenario: Missing media returns 404
- **WHEN** `GET /v1/media/{source_id}` is called for a source with no playable media
- **THEN** the engine responds `404`

### Requirement: Media-prep progress events
The engine SHALL publish media-preparation progress on the shared SSE event stream (`GET /v1/events`). Media-prep events SHALL carry the `source_id` and SHALL NOT carry a `job_id`, preserving the separation already observed between job events (which carry `job_id`) and non-job events. A terminal `ready` (or failure) event SHALL be published when a lazy download finishes.

#### Scenario: Download progress reaches subscribers
- **WHEN** a lazy media download advances and completes
- **THEN** media-prep events carrying `source_id` (and no `job_id`) are published over `/v1/events`, ending with a `ready` event

