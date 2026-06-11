# key-management

## ADDED Requirements

### Requirement: Key test endpoint
The engine SHALL expose `POST /v1/keys/test` accepting `{provider, api_key?}` that performs a minimal completion round-trip against the provider using, in order: the supplied key, the pushed in-memory key, or the provider's env variable. It SHALL return `{ok: true}` on success or `{ok: false, detail}` with a sanitized detail on failure. The response and engine logs SHALL never contain the key or provider response bodies (the established redaction discipline), and the tested key SHALL NOT be stored unless separately pushed via `PUT /v1/keys`.

#### Scenario: Valid key tests successfully
- **WHEN** a key valid for the selected provider is tested
- **THEN** the engine responds `{ok: true}` after a real provider round-trip

#### Scenario: Invalid key fails with sanitized detail
- **WHEN** an invalid key is tested and the provider returns an auth error echoing key material
- **THEN** the engine responds `{ok: false}` with a detail containing neither the key nor the provider response body

#### Scenario: Testing does not store
- **WHEN** a key is tested but not pushed
- **THEN** subsequent jobs do not use that key

#### Scenario: Unknown provider rejected
- **WHEN** the request names a provider not in the registry
- **THEN** the engine responds 400 without any outbound request
