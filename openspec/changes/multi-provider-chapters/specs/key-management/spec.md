# key-management

## ADDED Requirements

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
