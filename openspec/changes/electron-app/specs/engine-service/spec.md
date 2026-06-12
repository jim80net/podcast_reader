# engine-service

## ADDED Requirements

### Requirement: Graceful shutdown endpoint
The engine SHALL expose `POST /v1/shutdown` (bearer-authenticated like all routes) that responds 202 and then stops the server gracefully, running the full shutdown path: terminate child processes, stop the job worker, and remove the discovery file. This provides a portable graceful stop for supervisors on platforms without POSIX signals. `serve_engine` SHALL configure uvicorn with a bounded graceful-shutdown window (`timeout_graceful_shutdown`, 3 s) so engine exit is bounded even when a supervisor leaves an SSE stream open (per P1). `JobStore.shutdown()` SHALL set a stopping flag, and a job that fails while the store is stopping SHALL be journaled `interrupted`, not `failed` (per P2).

#### Scenario: Shutdown stops the engine cleanly
- **WHEN** `POST /v1/shutdown` is called with a valid token
- **THEN** the engine responds 202, terminates running children, and the process exits with the discovery file removed

#### Scenario: Open SSE stream cannot block shutdown (per P1)
- **WHEN** `POST /v1/shutdown` is called while a live `/v1/events` subscriber remains attached
- **THEN** the engine still exits within the bounded graceful-shutdown window and the full cleanup path runs (discovery file removed)

#### Scenario: Unauthenticated shutdown rejected
- **WHEN** `POST /v1/shutdown` is called without a valid token
- **THEN** the engine responds 401 and keeps serving

#### Scenario: Shutdown mid-job interrupts recoverably
- **WHEN** shutdown is requested while a job is running
- **THEN** the engine still exits; `JobStore.shutdown()` sets its stopping flag so a job that fails while stopping is journaled `interrupted` rather than `failed` (per P2), and any job still marked running on next start is recovered as `interrupted` per the existing recovery semantics
