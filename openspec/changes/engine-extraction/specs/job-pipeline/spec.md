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
- **THEN** it invokes the shared pipeline with a print-adapter event consumer and produces the same artifacts as the engine would for the same source

### Requirement: Chapters fault isolation
Any error in the chapters step (provider failure, truncation, malformed output, missing key) SHALL NOT fail the job or the CLI run: the pipeline SHALL record a structured warning and proceed to render a chapterless transcript. This applies to both engine and CLI execution.

#### Scenario: Chapter generation fails in engine
- **WHEN** chapter generation raises during an engine job
- **THEN** the job completes `done` with an HTML artifact without chapters and a `chapters_failed` warning on the record

#### Scenario: Chapter generation fails in CLI
- **WHEN** chapter generation raises during a CLI one-shot run
- **THEN** the CLI writes the chapterless HTML, prints the warning, and exits 0

### Requirement: Interruption semantics
Jobs found in `running` state when the engine starts SHALL be marked `interrupted` and be retryable.

#### Scenario: Crash recovery marks interrupted
- **WHEN** the engine starts and the job store contains a job in `running` state
- **THEN** that job is transitioned to `interrupted`

### Requirement: Single-job execution
The engine SHALL run at most one job at a time, queueing additional submissions in FIFO order.

#### Scenario: Second submission queues
- **WHEN** a job is running and another is submitted
- **THEN** the second job remains `queued` until the first reaches a terminal state
