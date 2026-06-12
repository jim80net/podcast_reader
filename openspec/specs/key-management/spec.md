# key-management Specification

## Purpose
TBD - created by archiving change multi-provider-chapters. Update Purpose after archive.
## Requirements
### Requirement: In-memory key store
The engine SHALL accept API keys via `PUT /v1/keys` (`{provider, api_key}`, bearer-auth like all routes) and hold them exclusively in process memory. Keys SHALL NOT be written to any file (settings, journal, library, discovery, logs) and SHALL NOT be readable back through any endpoint.

#### Scenario: Key accepted and used
- **WHEN** a key is PUT for the configured chapter provider and a job runs
- **THEN** chapter generation uses that key

#### Scenario: Keys are write-only
- **WHEN** any endpoint response or persisted file is inspected after a key is PUT
- **THEN** the key value appears nowhere

#### Scenario: Keys do not survive restart
- **WHEN** the engine restarts after a key was PUT and no provider env var is set
- **THEN** the key is gone and the next job's chapters step skips with `chapters_skipped` (the supervisor re-pushes keys at engine start, per the parent design)

### Requirement: Environment fallback for headless deployments
When no key has been pushed for the configured provider, the engine job runner SHALL fall back to the provider's key environment variable, preserving the behavior of headless `podcast-reader serve` deployments that export `ANTHROPIC_API_KEY` today. A pushed key takes precedence over the environment.

#### Scenario: Headless env var still works
- **WHEN** the engine runs with `ANTHROPIC_API_KEY` exported and no key pushed
- **THEN** an engine job generates chapters using the env key

#### Scenario: Pushed key wins over env
- **WHEN** both a pushed key and the env var are present
- **THEN** the pushed key is used

### Requirement: Provider selection setting
`EngineSettings` SHALL include `chapter_provider` (default `anthropic`) and `custom_provider_url` (default empty), both settable via `PUT /v1/settings` and snapshotted at job dequeue like all settings. `chapter_model` SHALL default to empty, meaning "the provider's default model".

#### Scenario: Provider change applies to next job
- **WHEN** `chapter_provider` is changed while a job is running
- **THEN** the running job keeps its snapshot; the next job uses the new provider

### Requirement: Stale settings upgrade cleanly
Engine settings persisted by an earlier version (lacking the new fields) SHALL load with defaults merged in, and `PUT /v1/settings` requests in the earlier shape SHALL continue to succeed. No job may fail because the settings file predates this change.

#### Scenario: Phase 1 settings file loads
- **WHEN** the engine starts with a `settings.json` written before this change
- **THEN** settings load with `chapter_provider="anthropic"` and jobs run normally

#### Scenario: Old-shape PUT succeeds
- **WHEN** a client PUTs a settings body without the new fields
- **THEN** the request succeeds and the new fields keep their current values

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

