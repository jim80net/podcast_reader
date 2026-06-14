# job-pipeline Specification

## Purpose
TBD - created by archiving change engine-extraction. Update Purpose after archive.
## Requirements
### Requirement: Job lifecycle states
A job SHALL move through the states: `queued` → `running` → (`done` | `failed` | `interrupted`), with `awaiting-confirmation` as an optional pre-`queued` state for externally initiated jobs. Failed jobs SHALL carry a structured `{code, message, hint}`.

#### Scenario: Successful job reaches done
- **WHEN** a job is submitted for a source that processes successfully
- **THEN** the job passes through `queued` and `running` and ends `done` with artifact references

#### Scenario: Failure carries structured error
- **WHEN** a pipeline step raises an unrecoverable error
- **THEN** the job ends `failed` with a machine-readable code, human message, and actionable hint

### Requirement: Step-level progress events
The job runner SHALL emit a typed event at minimum on each step start, step completion, warning, and terminal state, and the engine SHALL expose these via SSE while also persisting them on the job record.

#### Scenario: Events observable mid-run
- **WHEN** a client subscribes to `GET /v1/events` during a running job
- **THEN** it receives step events for that job as they occur

#### Scenario: Job record is source of truth
- **WHEN** a client fetches `GET /v1/jobs/{id}` after missing events
- **THEN** the response contains the job's current state and accumulated events

### Requirement: Shared pipeline orchestration
The CLI one-shot mode and the engine job runner SHALL execute the same pipeline implementation, differing only in the event consumer (stdout printing vs job store).

#### Scenario: CLI uses the shared pipeline
- **WHEN** the CLI one-shot mode runs
- **THEN** it invokes the shared pipeline with a print-adapter event consumer and produces equivalent artifact contents (transcript JSON, chapters, HTML) to an engine job for the same source, differing only in storage location and naming

### Requirement: Chapters fault isolation
Any error in the chapters step (provider failure, truncation, malformed output, missing key) SHALL NOT fail the job or the CLI run: the pipeline SHALL record a structured warning and proceed to render a chapterless transcript. This applies to both engine and CLI execution.

#### Scenario: Chapter generation fails in engine
- **WHEN** chapter generation raises during an engine job
- **THEN** the job completes `done` with an HTML artifact without chapters and a `chapters_failed` warning on the record

#### Scenario: Chapter generation fails in CLI
- **WHEN** chapter generation raises during a CLI one-shot run
- **THEN** the CLI writes the chapterless HTML, prints the warning, and exits 0

### Requirement: Job persistence
Job records, including their accumulated events, SHALL persist across engine restarts in a journal owned and written solely by the engine, with atomic writes (temp file + rename) on every state transition.

#### Scenario: Records survive restart
- **WHEN** the engine restarts after completing a job
- **THEN** `GET /v1/jobs/{id}` for that job returns its terminal state and events

### Requirement: Interruption semantics
Jobs found in `running` state when the engine starts SHALL be marked `interrupted`. Retry is an idempotent re-submission: a `POST /v1/jobs` with the same source reuses cached artifacts and creates a new job.

#### Scenario: Crash recovery marks interrupted
- **WHEN** the engine starts and the job journal contains a job in `running` state
- **THEN** that job is transitioned to `interrupted`

#### Scenario: Retry via re-submission
- **WHEN** a source whose previous job ended `interrupted` is submitted again
- **THEN** a new job runs, reusing any valid cached artifacts

### Requirement: Single-job execution
The engine SHALL run at most one job at a time, queueing additional submissions in FIFO order.

#### Scenario: Second submission queues
- **WHEN** a job is running and another is submitted
- **THEN** the second job remains `queued` until the first reaches a terminal state

### Requirement: Awaiting-confirmation state
The `awaiting-confirmation` state SHALL be reachable through the API: `POST /v1/jobs` SHALL accept `requires_confirmation` (default false; existing clients unchanged), and a true value SHALL journal the job in `awaiting-confirmation` without enqueueing it for execution. `POST /v1/jobs/{id}/confirm` SHALL transition an awaiting-confirmation job to `queued` and enqueue it (409 from any other state). `DELETE /v1/jobs/{id}` SHALL discard a job only while it is in `awaiting-confirmation` (409 otherwise). Jobs in `awaiting-confirmation` SHALL survive engine restarts without being enqueued, and SHALL never run without an explicit confirm.

#### Scenario: Default submission stays queued
- **WHEN** `POST /v1/jobs` is called without `requires_confirmation`
- **THEN** the job enters `queued` exactly as before this change

#### Scenario: Confirmation-required job does not execute
- **WHEN** a job is submitted with `requires_confirmation: true`
- **THEN** it remains `awaiting-confirmation` and no pipeline step runs

#### Scenario: Confirm enqueues
- **WHEN** `POST /v1/jobs/{id}/confirm` is called on an awaiting-confirmation job
- **THEN** the job transitions to `queued` and executes in FIFO order

#### Scenario: Confirm rejected in other states
- **WHEN** confirm is called on a job that is queued, running, or terminal
- **THEN** the engine responds 409 and the job is unchanged

#### Scenario: Dismiss discards only pending confirmations
- **WHEN** `DELETE /v1/jobs/{id}` is called on an awaiting-confirmation job
- **THEN** the job is removed from the journal; the same call on any other state responds 409

#### Scenario: Pending confirmation survives restart
- **WHEN** the engine restarts with an awaiting-confirmation job in the journal
- **THEN** the job is still `awaiting-confirmation` and has not been enqueued

### Requirement: Transcript artifact carries playback-sync metadata
The rendered transcript HTML SHALL carry machine-readable timestamp metadata and a playback-sync script so a host media player can synchronize with it. Each passage element and chapter section SHALL include `data-start` (and `data-end`) seconds. The artifact SHALL include an inline sync script that, when hosted inside the app, posts a seek message on passage click and highlights/scrolls the current passage on time updates; when the artifact is opened standalone (no parent window), the script SHALL no-op so the file remains self-contained and behaves exactly as before. The output SHALL be identical whether produced by the CLI or the engine, and the additions SHALL be inert for any existing consumer that ignores them.

#### Scenario: Passages carry timestamps
- **WHEN** the transcript HTML is rendered
- **THEN** each passage and chapter section carries `data-start` (and `data-end`) seconds

#### Scenario: Sync script is inert standalone
- **WHEN** the rendered artifact is opened directly in a browser with no parent player
- **THEN** the sync script does nothing and the page renders and behaves as it did before this change

#### Scenario: CLI and engine output match
- **WHEN** the same transcript is rendered by the CLI and by the engine
- **THEN** the produced HTML, including the sync metadata and script, is identical

