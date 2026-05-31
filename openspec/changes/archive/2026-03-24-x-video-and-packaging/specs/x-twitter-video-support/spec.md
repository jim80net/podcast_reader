## ADDED Requirements

### Requirement: Full pipeline for X/Twitter URLs
A user SHALL be able to run `podcast-reader "https://x.com/.../status/123" "Title"` and receive a complete styled HTML transcript plus optional chapters.

#### Scenario: End-to-end X post transcription
- **WHEN** a valid public X status URL with video is provided
- **THEN** audio is downloaded via yt-dlp, transcribed via whisper, chapters generated (if ANTHROPIC_API_KEY), and HTML written

#### Scenario: Caching still works
- **WHEN** the output JSON already exists for the platform ID stem
- **THEN** the pipeline skips the yt-dlp and whisper steps (same behavior as local files)

### Requirement: No separate "direct audio URL" code path
Direct HTTP audio URLs SHALL be handled uniformly by the yt-dlp path (no special curl/urllib branch).

#### Scenario: Direct mp3 link
- **WHEN** user passes a direct https://example.com/episode.mp3 URL
- **THEN** yt-dlp downloads it (or skips if already present) and the rest of the pipeline proceeds identically to a local file
