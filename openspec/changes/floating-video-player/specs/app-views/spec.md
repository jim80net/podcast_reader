# app-views Specification (delta)

## ADDED Requirements

### Requirement: Reader hosts the synchronized media player
The Reader view SHALL mount the floating media player beside the existing sandboxed transcript artifact and wire the bidirectional sync bridge between them, tearing both down on view cleanup. The view SHALL obtain the player kind and preparation status via `mediaInfo` and SHALL set the media element's source only once the media is `ready`, so the element never points at a half-written file. While a remote source is `preparing`, the view SHALL show a preparing indication and SHALL transition to playback on the media-prep `ready` event, with an info re-fetch as a fallback if the event is missed. The existing requirement that the artifact renders in an opaque-origin sandbox (no same-origin, no preload bridge, no token) SHALL remain unchanged; the sync bridge SHALL operate purely over `postMessage`.

#### Scenario: Opening an entry mounts the player
- **WHEN** an entry with playable media is opened in the Reader
- **THEN** the floating player mounts beside the transcript and the two are synchronized

#### Scenario: Preparing state resolves to playback
- **WHEN** a remote entry whose media is not yet cached is opened
- **THEN** the Reader shows a preparing indication and begins playback once the media is ready

#### Scenario: Player is torn down on leaving the Reader
- **WHEN** the user navigates away from the Reader
- **THEN** the player and the sync bridge are disposed along with the view

#### Scenario: Artifact isolation is preserved
- **WHEN** the synchronized player is active
- **THEN** the transcript artifact still runs in its opaque-origin sandbox with no token, no preload bridge, and no same-origin access — coupling happens only through `postMessage`
