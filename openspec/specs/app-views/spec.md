# app-views Specification

## Purpose
TBD - created by archiving change electron-app. Update Purpose after archive.
## Requirements
### Requirement: Library view
The Library view SHALL list the engine's library entries (`GET /v1/library`) as cards showing at least title, source, and date, and SHALL open an entry in the Reader view. An empty library SHALL show a call-to-action toward the New view.

#### Scenario: Completed transcript appears
- **WHEN** a job completes while the app is open
- **THEN** the Library view shows the new entry without an app restart

#### Scenario: Entry opens the Reader
- **WHEN** the user activates a library card
- **THEN** the Reader view renders that entry's transcript

### Requirement: Reader view renders the engine artifact in isolation
The Reader view SHALL display the engine's rendered HTML artifact (`GET /v1/transcripts/{id}.html`, fetched by the main process with auth) verbatim inside a sandboxed embedded surface that permits the artifact's inline scripts but grants no same-origin privileges, no preload bridge, and no access to the engine token. The reading experience is the `html.py` output unmodified.

#### Scenario: Artifact displays with working chapter navigation
- **WHEN** a transcript with chapters is opened
- **THEN** the artifact renders with its own styles and its inline scroll-sync script functioning

#### Scenario: Artifact cannot reach app internals
- **WHEN** script inside the artifact executes
- **THEN** it has no access to the parent window, IPC bridge, or engine credentials

### Requirement: New view submits and tracks jobs
The New view SHALL accept a pasted URL or a dropped local file (resolving the real filesystem path via the preload bridge), submit it as an engine job, and display step-level progress live from the event stream with job-record hydration as the fallback. Failed jobs SHALL display the structured `{code, message, hint}`; interrupted jobs SHALL offer a one-click retry (idempotent re-submission).

#### Scenario: Pasted URL runs with visible steps
- **WHEN** the user pastes a URL and submits
- **THEN** a job is created and each pipeline step's start/finish is shown as it happens

#### Scenario: Dropped file submits by path
- **WHEN** the user drops a local audio file onto the view
- **THEN** a job is submitted for that file's absolute path

#### Scenario: Failure shows the hint
- **WHEN** a job fails
- **THEN** the view shows the error message and its actionable hint

#### Scenario: Interrupted job retried in one click
- **WHEN** a job shows `interrupted` and the user clicks retry
- **THEN** a new job for the same source is submitted

### Requirement: Awaiting-confirmation surfacing
The New view SHALL list all jobs in `awaiting-confirmation` with their source URL visible, offering Run (confirm) and Dismiss actions, and SHALL surface them on app focus when they arrived via the protocol handler. Pending confirmations SHALL persist across app restarts (engine-journaled state).

#### Scenario: Confirm runs the job
- **WHEN** the user clicks Run on an awaiting-confirmation job
- **THEN** the job is confirmed via the engine and proceeds through the normal queue

#### Scenario: Dismiss discards the job
- **WHEN** the user clicks Dismiss
- **THEN** the job is removed and never executes

#### Scenario: Pending confirmation survives restart
- **WHEN** the app restarts while a job awaits confirmation
- **THEN** the New view still lists it

### Requirement: Settings view
The Settings view SHALL expose: chapter provider dropdown populated from the engine's `GET /v1/providers` (provider ids, default models, key-availability — per P4), including custom with base-URL field, per-provider API key entry (write-only — saved keys are shown masked and never read back) with a "test key" button calling the engine's key-test endpoint, whisper model/device/language, sentences per paragraph, and library/storage directory. Saving SHALL persist engine settings via `PUT /v1/settings` and key changes via the vault-and-push flow; validation errors from the engine SHALL be shown inline.

#### Scenario: Key test reports outcome without exposing the key
- **WHEN** the user enters a key and clicks test
- **THEN** a success or sanitized failure is displayed, and the key value appears in no log or response surface

#### Scenario: Provider switch persists
- **WHEN** the user selects a different provider and saves
- **THEN** `GET /v1/settings` reflects the new `chapter_provider` and the next job uses it

#### Scenario: Invalid setting rejected inline
- **WHEN** the engine rejects a settings value (e.g. invalid custom provider URL)
- **THEN** the view shows the engine's error next to the offending field and persists nothing

