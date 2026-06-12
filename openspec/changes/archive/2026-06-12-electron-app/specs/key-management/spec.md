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

### Requirement: Provider listing endpoint (per P4)
The engine SHALL expose `GET /v1/providers` (bearer-authenticated like all routes) returning, for each provider registry entry, the provider id, its default model, and a boolean indicating whether a key is currently available for it (pushed in-memory or present in the provider's env variable). The response SHALL never contain key material in any form — no values, prefixes, lengths, or fingerprints, only the availability boolean.

#### Scenario: Registry listed
- **WHEN** `GET /v1/providers` is called with a valid token
- **THEN** the response lists exactly the registry ids `anthropic`, `openai`, `xai`, `openrouter`, `deepseek`, and `custom`, each with its default model and key-availability boolean

#### Scenario: No key material in the listing
- **WHEN** `GET /v1/providers` is called while keys are pushed and provider env variables are set
- **THEN** the response contains no key value or key-derived material — availability is reported as a boolean only
