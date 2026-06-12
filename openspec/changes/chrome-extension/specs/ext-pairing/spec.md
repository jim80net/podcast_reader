# ext-pairing

## ADDED Requirements

### Requirement: User-mediated pairing
The extension popup SHALL provide a pairing form accepting the combined `<port>-<code>` paste string as the primary input, with separate port and 6-character-code fields as fallback (per review adjudication, matching the app's display), exchange the code via `POST /v1/pair/claim` at `http://127.0.0.1:<port>`, verify the received token with an authed `GET /v1/health`, and persist `{port, token}` in `chrome.storage.local` only after verification succeeds. A failed claim or verification SHALL leave any previously stored pairing untouched and show a self-authored error with a retry affordance.

#### Scenario: Successful pairing stores the connection
- **WHEN** the user enters the port and a valid unexpired code
- **THEN** the popup stores `{port, token}` and shows the connected state

#### Scenario: Wrong code leaves state unchanged
- **WHEN** the user enters an invalid code
- **THEN** nothing is stored (any prior pairing is kept) and the popup offers to retry

### Requirement: Credential confinement and least privilege
The bearer token SHALL exist only in `chrome.storage.local` (never `storage.sync`) and in popup/service-worker memory, SHALL travel only in `Authorization` headers (never URLs), and SHALL never reach a web-page context: the extension SHALL declare no content scripts. The manifest SHALL request only `storage`, `alarms`, `notifications`, `contextMenus`, and `activeTab` permissions with `host_permissions` limited to `http://127.0.0.1/*`; the `cookies` permission and site origins SHALL appear only under optional permissions, requested on demand (per ext-cookie-capture).

#### Scenario: No content scripts in the manifest
- **WHEN** the built manifest is inspected
- **THEN** it declares no `content_scripts` and its `host_permissions` contain only `http://127.0.0.1/*`

#### Scenario: Token never synced or query-passed
- **WHEN** extension code paths handling the token are exercised under test
- **THEN** the token is written only to `chrome.storage.local` and sent only as an `Authorization` header

### Requirement: Reconnection and re-pairing
Because the engine port is fixed per install, the extension SHALL treat the stored `{port, token}` as durable: on use it SHALL probe `GET /v1/health`. A connection failure SHALL be presented as "the desktop app isn't running" with a launch affordance; a 401 SHALL be presented as "pairing expired" with a clear-and-re-pair affordance. The extension SHALL NOT scan ports or attempt any token recovery other than re-running pairing.

#### Scenario: Engine down offers launch
- **WHEN** the popup opens and the stored port refuses connection
- **THEN** the popup shows the app-not-running state with a launch affordance and keeps the stored pairing

#### Scenario: Rotated token forces re-pair
- **WHEN** a request returns 401 (engine token rotated)
- **THEN** the popup shows the re-pair flow, and completing it replaces the stored pairing
