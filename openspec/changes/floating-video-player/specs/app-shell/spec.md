# app-shell Specification (delta)

## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Credential-free renderer
The renderer SHALL run with context isolation enabled, node integration disabled, and sandboxing on, and SHALL communicate with the main process only through the typed preload bridge and main-registered privileged URL schemes — never directly with the engine. The engine bearer token SHALL exist only in the main process; all engine HTTP/SSE traffic SHALL originate there, with events forwarded to the renderer over IPC and media bytes served through the main-mediated privileged media scheme, whose handler adds the token the renderer never holds. The renderer SHALL never possess the engine token by any path, and no renderer-originated request SHALL ever carry the token to the engine.

#### Scenario: Token absent from renderer
- **WHEN** the renderer context is inspected during e2e tests
- **THEN** the bearer token is not reachable from any renderer-accessible API or global

#### Scenario: Renderer reaches the engine only through main
- **WHEN** any view needs engine data or media
- **THEN** the request flows either through the preload bridge to the main-process engine client, or through a main-registered privileged scheme whose handler runs in the main process — never as a direct renderer-to-engine connection bearing the token
