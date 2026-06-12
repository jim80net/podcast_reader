# ext-jobs

## ADDED Requirements

### Requirement: Submit the current page without a confirmation gate
The extension SHALL submit the active tab's URL as an engine job from the popup's submit affordance and from a context-menu item on any page (URL from `info.pageUrl`). Because the manifest sets `default_popup`, `chrome.action.onClicked` never fires (per U1; https://developer.chrome.com/docs/extensions/reference/api/action): the toolbar click opens the popup and grants `activeTab`, and the popup SHALL read the active tab's URL via `chrome.tabs.query` for its submit affordance. Submission SHALL use `POST /v1/jobs` with `requires_confirmation: false`: a deliberate click inside the user-installed, token-authed extension carries user intent exactly as the app's New view does, while the confirmation gate remains scoped to the unauthenticated `podcast-reader://` channel (per the parent design's F10). Submitted job ids SHALL be recorded in `chrome.storage.local` as a bounded most-recent-first list.

#### Scenario: Popup submit affordance submits the active tab (per U1)
- **WHEN** the user clicks the toolbar action (opening the popup) and invokes the popup's submit affordance while paired and the engine is reachable
- **THEN** a job for the active tab's URL (read via `chrome.tabs.query` under the click-granted `activeTab`) is created in `queued` (no `awaiting-confirmation` stop) and its id is tracked

#### Scenario: Context menu submits the page
- **WHEN** the user invokes the context-menu item on any page
- **THEN** the page URL is submitted the same way, without requiring host permissions for that page

### Requirement: Popup progress via hydration then stream
On every popup open, the popup SHALL first hydrate tracked jobs from the job records (`GET /v1/jobs/{id}`), then attach a streamed `GET /v1/events` consumer using `fetch()` + ReadableStream with the `Authorization` header, rendering live step progress. The stream SHALL live and die with the popup; the service worker SHALL never hold an events stream. Job state rendered after a popup reopen SHALL be correct even if every event was missed while closed. Engine-supplied and page-derived strings (titles, messages, hints, URLs) SHALL reach the popup DOM via `textContent` only — never as markup — because the popup is the token-holding context (per U7).

#### Scenario: Reopened popup shows current state
- **WHEN** a job progresses while the popup is closed and the popup is reopened
- **THEN** the popup shows the job's current step/state from hydration, then resumes live updates

#### Scenario: No service-worker stream
- **WHEN** the popup closes during a running job
- **THEN** no `/v1/events` connection remains open from the extension

### Requirement: Completion notification via alarms polling
While any tracked job is non-terminal, the service worker SHALL maintain a `chrome.alarms` alarm (30-second period, Chrome's floor) that polls the tracked jobs' records and SHALL raise a `chrome.notifications` notification when a job reaches a terminal state (`done`, `failed`, `interrupted`), clearing the alarm when no tracked job remains non-terminal. Polling SHALL be stateless across service-worker restarts (state read from `chrome.storage.local` on each wake).

#### Scenario: Completion notifies with the popup closed
- **WHEN** a tracked job reaches `done` while the popup is closed
- **THEN** a notification announces the completion within one polling period and the alarm is cleared once nothing remains in flight

#### Scenario: Service-worker restart does not lose tracking
- **WHEN** the service worker is terminated and later woken by the alarm
- **THEN** it resumes polling the tracked jobs from storage with no missed terminal transition

### Requirement: Protocol fallback when the engine is unreachable
When a submission attempt fails because the engine is unreachable, the extension SHALL offer to open `podcast-reader://transcribe?url=<page URL>` — launching the desktop app and landing the job in `awaiting-confirmation` via the app's existing protocol path (confirm-gated, because that channel is unauthenticated). The extension SHALL NOT silently queue or retry submissions itself.

#### Scenario: Engine down falls back to the protocol
- **WHEN** the user submits a tab while the engine is not running and accepts the fallback
- **THEN** the protocol URL is opened, and once the app starts the job appears in `awaiting-confirmation` showing that URL
