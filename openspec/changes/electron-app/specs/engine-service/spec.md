# engine-service

## ADDED Requirements

### Requirement: Graceful shutdown endpoint
The engine SHALL expose `POST /v1/shutdown` (bearer-authenticated like all routes) that responds 202 and then stops the server gracefully, running the full shutdown path: terminate child processes, stop the job worker, and remove the discovery file. This provides a portable graceful stop for supervisors on platforms without POSIX signals.

#### Scenario: Shutdown stops the engine cleanly
- **WHEN** `POST /v1/shutdown` is called with a valid token
- **THEN** the engine responds 202, terminates running children, and the process exits with the discovery file removed

#### Scenario: Unauthenticated shutdown rejected
- **WHEN** `POST /v1/shutdown` is called without a valid token
- **THEN** the engine responds 401 and keeps serving

#### Scenario: Shutdown mid-job interrupts recoverably
- **WHEN** shutdown is requested while a job is running
- **THEN** the engine still exits, and on next start that job is marked `interrupted` per the existing recovery semantics
