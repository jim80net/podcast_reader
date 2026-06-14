# media-playback Specification

## Purpose
TBD - created by archiving change floating-video-player. Update Purpose after archive.
## Requirements
### Requirement: Floating media player surface
The Reader view SHALL host a floating media player layered over the transcript: a panel that is draggable by a handle and resizable, whose position and size persist across mounts within a session. The player SHALL render `<video controls>` for video sources, a compact `<audio controls>` bar for audio-only sources, and an embedded YouTube player for YouTube sources, behind one uniform `{seekTo(t), onTime(cb)}` interface. The player SHALL be collapsible to a minimized affordance and SHALL NOT obstruct use of the transcript when minimized.

#### Scenario: Player persists geometry
- **WHEN** the user drags and resizes the player, leaves the Reader, and reopens an entry
- **THEN** the player reappears at the last position and size

#### Scenario: Audio-only entry gets the audio skin
- **WHEN** an entry whose media has no video track is opened
- **THEN** the player renders the compact audio bar, not an empty video frame, and sync still functions

#### Scenario: Minimized player frees the transcript
- **WHEN** the player is minimized
- **THEN** the full transcript is usable and the player can be restored

### Requirement: Source classification selects the player kind
The engine SHALL classify each library entry's source into exactly one player kind — `youtube`, `video`, `audio`, or `unavailable` — and the renderer SHALL choose its render path from that kind alone, never by parsing the source URL itself. YouTube sources SHALL be played by embedding a cross-origin `youtube-nocookie` iframe driven by the raw YouTube iframe `postMessage` control protocol; no YouTube JavaScript SHALL be loaded into the renderer's main world. Sources classified `unavailable` SHALL leave the Reader transcript-only with no player.

#### Scenario: YouTube source plays without third-party JS in the renderer
- **WHEN** a YouTube entry is opened
- **THEN** the player embeds a `youtube-nocookie` iframe and controls it via `postMessage`, and no script from youtube.com is loaded into the renderer document

#### Scenario: Unplayable source degrades to transcript-only
- **WHEN** an entry's source cannot yield playable media
- **THEN** the Reader shows the transcript with no player and no error spinner

### Requirement: Lazy media acquisition and bounded cache
For non-YouTube remote sources, the engine SHALL acquire the media on first demand, not at transcription time. The first info or byte request for an uncached remote source SHALL report `preparing` and start a single-flight download keyed by `source_id` — concurrent requests for the same id SHALL join the one in-flight download rather than starting duplicates — reusing the yt-dlp download path with a video-capable format selector that falls back to the best single stream when there is no video track. Downloads SHALL stage to an identity-bound `.part` file and SHALL be discarded, never served, on failure or interruption. Completed media SHALL be cached under the data dir and evicted least-recently-used against a configurable cap (`EngineSettings.media_cache_max_bytes`, default 5 GiB), with eviction enforced on insertion. The lazy-download path SHALL be independent of the FIFO job worker and SHALL NOT block transcription jobs.

#### Scenario: First watch downloads, second watch is cached
- **WHEN** a remote entry is opened for playback the first time
- **THEN** the engine reports `preparing`, downloads the media once, caches it, and serves it; a subsequent watch serves from cache without re-downloading

#### Scenario: Concurrent requests join one download
- **WHEN** two playback requests for the same uncached `source_id` arrive close together
- **THEN** a single download runs and both requests are served from it

#### Scenario: Interrupted download leaves no servable partial
- **WHEN** a lazy download is interrupted (engine restart or network failure)
- **THEN** the `.part` is discarded, no partial file is ever served, and the next request restarts the download cleanly

#### Scenario: Cache stays under the cap
- **WHEN** adding a newly downloaded file would exceed `media_cache_max_bytes`
- **THEN** least-recently-used cached media is evicted until the cache is within the cap

#### Scenario: Lazy download does not block jobs
- **WHEN** a media download is in progress and a transcription job is submitted
- **THEN** the job runs on the job worker without waiting for the download

### Requirement: Transcript and media stay synchronized
The transcript and the media player SHALL be bidirectionally synchronized over a channel-tagged `postMessage` protocol between the renderer and the opaque-origin transcript iframe. Clicking a transcript passage SHALL seek the player to that passage's start. As the media plays, the passage whose gap-free time range contains the current playback position SHALL be highlighted and scrolled into view. Because the transcript iframe is opaque-origin and the YouTube iframe posts its own control messages to the same window, the renderer SHALL validate every sync message by both the channel tag and `event.source` identity. Passage time ranges SHALL be gap-free: a passage's effective end SHALL be the next passage's start (the last clamped to media duration), so playback never falls between passages.

#### Scenario: Click a passage to seek
- **WHEN** the user clicks a transcript passage
- **THEN** the player seeks to that passage's start time

#### Scenario: Playback follows the transcript
- **WHEN** the media plays past a passage boundary
- **THEN** the newly current passage is highlighted and scrolled into view

#### Scenario: Foreign and cross-frame messages are ignored
- **WHEN** a `message` event arrives that lacks the `pr-sync` channel tag or does not originate from the transcript iframe (e.g. a YouTube control message)
- **THEN** it is ignored by the transcript-sync handler

#### Scenario: Standalone artifact does not break
- **WHEN** the transcript artifact HTML is opened directly in a browser (no parent player)
- **THEN** its sync script no-ops and the page behaves exactly as before

