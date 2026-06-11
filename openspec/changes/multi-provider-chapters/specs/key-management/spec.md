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
- **WHEN** the engine restarts after a key was PUT
- **THEN** the key is gone and the next job's chapters step skips with `chapters_skipped` (the supervisor re-pushes keys at engine start, per the parent design)

### Requirement: Provider selection setting
`EngineSettings` SHALL include `chapter_provider` (default `anthropic`) and `custom_provider_url` (default empty), both settable via `PUT /v1/settings` and snapshotted at job dequeue like all settings.

#### Scenario: Provider change applies to next job
- **WHEN** `chapter_provider` is changed while a job is running
- **THEN** the running job keeps its snapshot; the next job uses the new provider
