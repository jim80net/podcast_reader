# engine-service Specification (delta)

## ADDED Requirements

### Requirement: Media info endpoint
The engine SHALL expose `GET /v1/media/{source_id}/info` (bearer-authenticated like all routes) returning `{kind, youtube_id, duration_s, status, progress}` where `kind ∈ {youtube, video, audio, unavailable}` and `status ∈ {ready, preparing, unavailable}`. Classification and probing SHALL live in the engine so no client parses source URLs. Probing SHALL NOT depend on `ffprobe` (which is not guaranteed in the frozen bundle): duration and the presence of a video track SHALL be determined via `ffmpeg` or from the yt-dlp format for remote sources. For an uncached remote source the endpoint SHALL report `status: preparing` and initiate the single-flight acquisition; for YouTube it SHALL return immediately with the extracted id.

#### Scenario: Info reports kind for a local video
- **WHEN** `GET /v1/media/{id}/info` is called for a local entry with a video track
- **THEN** it returns `kind: video`, a duration, and `status: ready`

#### Scenario: Info kicks off lazy preparation
- **WHEN** `GET /v1/media/{id}/info` is called for an uncached remote source
- **THEN** it returns `status: preparing` and starts the download, without blocking on completion

#### Scenario: YouTube info is immediate
- **WHEN** `GET /v1/media/{id}/info` is called for a YouTube source
- **THEN** it returns `kind: youtube` with `youtube_id` and does not download any media

### Requirement: Media byte-serving endpoint with Range
The engine SHALL expose `GET /v1/media/{source_id}` (bearer-authenticated) serving the cached or local media bytes with HTTP Range support — honoring the `Range` request header with `206 Partial Content` and `Content-Range`/`Accept-Ranges` so the player can seek — implemented with a Range-capable file response rather than a non-Range streaming response. A request for media that does not exist or cannot be produced SHALL return `404`.

#### Scenario: Range request returns partial content
- **WHEN** `GET /v1/media/{id}` is called with a `Range` header for a ready media file
- **THEN** the engine responds `206` with the requested byte range and `Content-Range`

#### Scenario: Missing media returns 404
- **WHEN** `GET /v1/media/{id}` is called for a source with no playable media
- **THEN** the engine responds `404`

### Requirement: Media-prep progress events
The engine SHALL publish media-preparation progress on the shared SSE event stream (`GET /v1/events`). Media-prep events SHALL carry the `source_id` and SHALL NOT carry a `job_id`, preserving the separation already observed between job events (which carry `job_id`) and non-job events. A terminal `ready` (or failure) event SHALL be published when a lazy download finishes.

#### Scenario: Download progress reaches subscribers
- **WHEN** a lazy media download advances and completes
- **THEN** media-prep events carrying `source_id` (and no `job_id`) are published over `/v1/events`, ending with a `ready` event
