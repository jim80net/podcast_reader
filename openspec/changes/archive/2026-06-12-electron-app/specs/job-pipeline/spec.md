# job-pipeline

## RENAMED Requirements

<!-- Renamed because the state is no longer "forward compatibility" — this change makes it reachable (per P5). -->
- FROM: `### Requirement: Awaiting-confirmation state (forward compatibility)`
- TO: `### Requirement: Awaiting-confirmation state`

## MODIFIED Requirements

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
