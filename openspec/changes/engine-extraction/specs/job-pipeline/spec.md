# job-pipeline

## ADDED Requirements

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

### Requirement: Awaiting-confirmation state (forward compatibility)
The state machine SHALL include `awaiting-confirmation`, though no Phase 1 API path creates such jobs (protocol-initiated jobs arrive with the desktop app and extension phases). State-machine unit tests SHALL cover it.

#### Scenario: State exists but unreachable via API
- **WHEN** any Phase 1 endpoint creates a job
- **THEN** the job enters `queued`, never `awaiting-confirmation`

### Requirement: Single-job execution
The engine SHALL run at most one job at a time, queueing additional submissions in FIFO order.

#### Scenario: Second submission queues
- **WHEN** a job is running and another is submitted
- **THEN** the second job remains `queued` until the first reaches a terminal state
