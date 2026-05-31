## Why

Podcast readers wanted to transcribe YouTube videos. Downloading audio just to run whisper was wasteful when the platform already provides high-quality captions. The project needed a fast path for YouTube that reuses existing captions instead of re-transcribing audio.

## What Changes

- Added first-class support for YouTube URLs: detect youtube.com / youtu.be, fetch captions via youtube-transcript-api, emit whisper-compatible JSON.
- Downstream pipeline (chapters, HTML) unchanged — YouTube path produces the same artifacts as local files.
- Added `--source` metadata so generated HTML indicates "youtube-captions" vs "whisper-ctranslate2".
- New module (originally `youtube_transcript.py`, later `podcast_reader.youtube`) with URL parsing, caption fetching, and format conversion.
- Updated CLI / shell entry point to route YouTube inputs to the new path and skip whisper entirely.

## Capabilities

### New Capabilities
- `youtube-captions`: Fetch existing YouTube captions (prefer manual over auto-generated) and convert them to the internal whisper JSON format for zero-cost transcription of YouTube content.

### Modified Capabilities
- None — this was additive; no existing requirements changed.

## Impact

- New runtime dependency: `youtube-transcript-api`
- New test surface for URL parsing and snippet conversion
- HTML output now carries transcript source attribution
- No impact on whisper, chapter, or diarization flows
