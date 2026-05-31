## ADDED Requirements

### Requirement: YouTube video URLs are recognized and routed to caption fetcher
The CLI SHALL detect YouTube URLs (youtube.com/watch, youtu.be, youtube.com/embed) and route them to the caption-based transcription path instead of audio download + whisper.

#### Scenario: Standard watch URL
- **WHEN** user invokes the tool with a https://www.youtube.com/watch?v=VIDEO_ID URL
- **THEN** the system fetches captions via youtube-transcript-api and produces <video_id>.json without invoking whisper-ctranslate2

#### Scenario: Short youtu.be URL
- **WHEN** user provides a https://youtu.be/VIDEO_ID URL
- **THEN** the video ID is correctly extracted and captions are fetched

#### Scenario: Embed URL
- **WHEN** user provides a https://www.youtube.com/embed/VIDEO_ID URL
- **THEN** the video ID is extracted and the caption path is used

### Requirement: Manual captions preferred over auto-generated
When both manual and auto-generated English captions exist, the system SHALL prefer the manually-created track.

#### Scenario: Video has manual English captions
- **WHEN** a video has both manually-created and auto-generated English transcripts
- **THEN** the manual transcript is returned

#### Scenario: Only auto-generated captions available
- **WHEN** a video only has auto-generated captions
- **THEN** the auto-generated track is used and transcription succeeds

### Requirement: Captions converted to whisper-compatible JSON
Fetched YouTube caption snippets SHALL be converted to the exact JSON shape produced by whisper-ctranslate2 so that chapters.py and html.py consume them without modification.

#### Scenario: Snippet to segment conversion
- **WHEN** youtube-transcript-api returns snippets with start, duration, text
- **THEN** the output JSON contains segments with start, end (=start+duration), and stripped text

#### Scenario: Empty or whitespace-only text is dropped
- **WHEN** a snippet contains only whitespace
- **THEN** it does not appear in the final segments array

### Requirement: HTML output attributes transcript source
The generated HTML SHALL indicate whether the transcript came from "youtube-captions" or "whisper-ctranslate2".

#### Scenario: YouTube source shown in meta
- **WHEN** the pipeline runs for a YouTube URL
- **THEN** the HTML contains a meta line and footer stating the source is youtube-captions
