## Context

The original pipeline only handled local audio files and direct HTTP audio URLs. Users frequently wanted to transcribe YouTube videos. Whisper re-transcription of YouTube audio was slow, lossy, and unnecessary because YouTube already exposes timed captions.

## Goals / Non-Goals

**Goals:**
- Support common YouTube URL formats (watch, youtu.be, embed) with a single fast path
- Prefer manually-created captions; fall back to auto-generated
- Emit exactly the same JSON shape as whisper-ctranslate2 so chapters.html, and everything downstream works unchanged
- Surface the transcript source in the final HTML for transparency

**Non-Goals:**
- Download YouTube audio for YouTube inputs (wasteful)
- Support non-English caption tracks in v1
- Handle age-restricted or members-only videos (requires auth/cookies)

## Decisions

- Use `youtube-transcript-api` (already popular, no ffmpeg dependency) rather than yt-dlp + whisper for this path.
- Convert YouTube snippet objects to the minimal whisper `{"segments": [{"start", "end", "text"}]}` shape in-process so the rest of the system is oblivious.
- Add a `--source` flag (or equivalent) only to the HTML renderer so attribution is visible without changing any other contract.
- Keep the change small: one new module + one branch in the input router + one metadata field.

## Risks / Trade-offs

- Caption quality varies by video; some auto-generated tracks are poor. Users who care can still force the whisper path by downloading audio first.
- Library may lag YouTube DOM changes (mitigation: small, well-maintained dependency).
- No speaker diarization on YouTube path (acceptable — the feature is for quick caption-based transcripts).
