# engine-service

## MODIFIED Requirements

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

## ADDED Requirements

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
