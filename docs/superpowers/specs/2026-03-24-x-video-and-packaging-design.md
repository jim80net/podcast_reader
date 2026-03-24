# X Video Support & Python Package Migration

**Date:** 2026-03-24
**Status:** Approved

## Goal

Add support for transcribing X/Twitter videos (and any other yt-dlp-supported platform) by:
1. Adding yt-dlp as a generic fallback for non-YouTube, non-direct-audio URLs
2. Migrating from a shell script entry point to a Python CLI package, enabling future distribution as a standalone binary

## Package Structure

```
podcast_reader/
├── pyproject.toml
├── src/
│   └── podcast_reader/
│       ├── __init__.py
│       ├── cli.py              # main entry point (replaces transcribe.sh)
│       ├── transcribe.py       # whisper-ctranslate2 orchestration
│       ├── youtube.py          # YouTube caption fetching (from youtube_transcript.py)
│       ├── ytdlp.py            # yt-dlp audio download + title fetch
│       ├── chapters.py         # chapter generation via Claude (from generate_chapters.py)
│       └── html.py             # HTML output (from json_to_html.py)
├── tests/
└── ...
```

## CLI Interface

```
podcast-reader <url-or-file> [title]
```

Entry point defined in `pyproject.toml`:
```toml
[project.scripts]
podcast-reader = "podcast_reader.cli:main"
```

### Environment Variables (unchanged)

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3` | Whisper model size |
| `WHISPER_LANG` | `en` | Language code |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `HF_TOKEN` | _(none)_ | HuggingFace token for diarization |
| `ANTHROPIC_API_KEY` | _(none)_ | Enables chapter generation via Claude |
| `SENTENCES` | `5` | Sentences per paragraph in HTML |

## URL Routing

The CLI determines the transcription method based on the input:

1. **YouTube URL** (youtube.com, youtu.be) → `youtube.py` — fetch existing captions via youtube-transcript-api. No download.
2. **URL with audio extension** (.mp3, .wav, .m4a, .ogg, .flac) → download with `urllib`, then whisper.
3. **Any other URL** → `ytdlp.py` — extract audio via yt-dlp, then whisper.
4. **Local file** → whisper directly.

## Module Specifications

### `cli.py` — Main Entry Point

Replaces all orchestration logic from `transcribe.sh`.

**Responsibilities:**
- Parse arguments (url-or-file, optional title) using argparse
- Read environment variables for config
- Route input to the correct handler based on URL detection
- Orchestrate the pipeline: transcript → chapters → HTML
- Cache: skip steps if output files already exist (same behavior as shell script)
- Print status messages and output file paths

**URL detection:**
- YouTube: regex matching `youtube.com/`, `youtu.be/`
- Audio URL: URL ending in known audio extensions
- Other URL: any `http://` or `https://` URL not matching above
- Local file: everything else

### `youtube.py` — YouTube Captions

Moved from `youtube_transcript.py`. No functional changes.

**Public API:**
- `extract_video_id(url: str) -> str | None`
- `fetch_transcript(video_id: str) -> list[dict]`
- `snippets_to_whisper_segments(snippets: list[dict]) -> dict`
- `fetch_video_title(video_id: str) -> str`

### `ytdlp.py` — yt-dlp Download (New)

Downloads audio from any yt-dlp-supported platform (X/Twitter, Vimeo, TikTok, etc.).

**Public API:**
- `download_audio(url: str, output_dir: Path) -> Path` — extracts audio as mp3, returns path to the file
- `fetch_title(url: str) -> str` — returns the video/post title

**Implementation:**
- Uses `subprocess.run` to call yt-dlp CLI
- `download_audio`: runs `yt-dlp -x --audio-format mp3 -o <output_template> <url>`
- `fetch_title`: runs `yt-dlp --print title <url>`
- Raises `RuntimeError` on yt-dlp failure with stderr output

### `transcribe.py` — Whisper Orchestration

Encapsulates the whisper-ctranslate2 invocation currently done in `transcribe.sh`.

**Public API:**
- `transcribe(audio_path: Path, output_dir: Path, model: str, lang: str, device: str, hf_token: str | None) -> Path` — runs whisper-ctranslate2, returns path to JSON output

**Implementation:**
- Uses `subprocess.run` to call `whisper-ctranslate2` CLI
- Constructs args from parameters (model, language, device, hf_token)

### `chapters.py` — Chapter Generation

Moved from `generate_chapters.py`. No functional changes.

**Public API:**
- `format_transcript(segments: list[dict]) -> str`
- `generate_chapters(transcript_text: str, model: str) -> list[dict]`
- `snap_chapters_to_segments(chapters: list[dict], segments: list[dict]) -> list[dict]`

### `html.py` — HTML Output

Moved from `json_to_html.py`. No functional changes.

**Public API:**
- `build_html(segments, title, chapters, sentences_per_para, source) -> str`
- All helper functions (segments_to_paragraphs, build_sidebar_nav, etc.)

## Dependencies (`pyproject.toml`)

```toml
[project]
name = "podcast-reader"
requires-python = ">=3.10"
dependencies = [
    "whisper-ctranslate2>=0.5.7",
    "pyannote.audio>=4.0",
    "anthropic>=0.40",
    "youtube-transcript-api>=1.0",
    "yt-dlp",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
]
```

## Pipeline Flow

```
Input URL/file
    │
    ├─ YouTube URL ──────────► youtube.py ──► whisper JSON
    ├─ Audio URL ────────────► urllib download ──► whisper-ctranslate2 ──► whisper JSON
    ├─ Other URL ────────────► ytdlp.py (extract audio) ──► whisper-ctranslate2 ──► whisper JSON
    └─ Local file ───────────► whisper-ctranslate2 ──► whisper JSON
                                                              │
                                              chapters.py (if ANTHROPIC_API_KEY set)
                                                              │
                                                          html.py ──► styled HTML
```

## Files Changed

| File | Action |
|------|--------|
| `pyproject.toml` | Create — dependencies + entry point |
| `src/podcast_reader/__init__.py` | Create — package init |
| `src/podcast_reader/cli.py` | Create — main entry point |
| `src/podcast_reader/transcribe.py` | Create — whisper orchestration |
| `src/podcast_reader/youtube.py` | Create — moved from `youtube_transcript.py` |
| `src/podcast_reader/ytdlp.py` | Create — yt-dlp integration |
| `src/podcast_reader/chapters.py` | Create — moved from `generate_chapters.py` |
| `src/podcast_reader/html.py` | Create — moved from `json_to_html.py` |
| `transcribe.sh` | Delete |
| `youtube_transcript.py` | Delete (moved) |
| `generate_chapters.py` | Delete (moved) |
| `json_to_html.py` | Delete (moved) |
| `requirements.txt` | Delete (replaced by pyproject.toml) |
| `CLAUDE.md` | Update — new structure, CLI usage |

## What's NOT Changing

- HTML output format and styling
- Chapter generation logic and prompts
- YouTube caption fetching behavior
- Whisper transcription parameters
- Environment variable interface
- Caching behavior (skip if output exists)
