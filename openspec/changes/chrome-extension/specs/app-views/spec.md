# app-views

## MODIFIED Requirements

### Requirement: Settings view
The Settings view SHALL expose: chapter provider dropdown populated from the engine's `GET /v1/providers` (provider ids, default models, key-availability — per P4), including custom with base-URL field, per-provider API key entry (write-only — saved keys are shown masked and never read back) with a "test key" button calling the engine's key-test endpoint, whisper model/device/language, sentences per paragraph, and library/storage directory. Saving SHALL persist engine settings via `PUT /v1/settings` and key changes via the vault-and-push flow; validation errors from the engine SHALL be shown inline. The view SHALL additionally provide an extension-pairing section — a button that mints a pairing code via the engine (`POST /v1/pair`, main-process IPC) and displays the engine port and code (with the combined `<port>-<code>` paste form) alongside an expiry countdown, re-minting on demand — and a cookie-management section listing captured cookie domains from `GET /v1/cookies` (metadata only) with per-domain delete via `DELETE /v1/cookies/{domain}`.

#### Scenario: Key test reports outcome without exposing the key
- **WHEN** the user enters a key and clicks test
- **THEN** a success or sanitized failure is displayed, and the key value appears in no log or response surface

#### Scenario: Provider switch persists
- **WHEN** the user selects a different provider and saves
- **THEN** `GET /v1/settings` reflects the new `chapter_provider` and the next job uses it

#### Scenario: Invalid setting rejected inline
- **WHEN** the engine rejects a settings value (e.g. invalid custom provider URL)
- **THEN** the view shows the engine's error next to the offending field and persists nothing

#### Scenario: Pairing code displayed for the extension
- **WHEN** the user clicks "Connect browser extension"
- **THEN** the view shows the engine port and a fresh 6-character code with its expiry, and clicking again replaces it with a new code

#### Scenario: Captured cookies listed and deletable
- **WHEN** cookie jars exist on the engine and the user opens Settings
- **THEN** the cookie section lists each domain with its capture date (no cookie values anywhere in the UI or IPC payloads), and deleting a domain removes it from the engine and the list
