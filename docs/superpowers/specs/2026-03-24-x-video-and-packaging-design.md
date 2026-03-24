# X Video Support & Python Package Migration

**Date:** 2026-03-24
**Status:** Approved

## Goal

Add support for transcribing X/Twitter videos (and any other yt-dlp-supported platform) by:
1. Adding yt-dlp as a generic fallback for non-YouTube, non-local-file URLs
2. Migrating from a shell script entry point to a Python CLI package, enabling future distribution as a standalone binary
3. Establishing strict code quality standards: type checking, formatting, linting, and comprehensive testing

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
│   ├── __init__.py
│   ├── fixtures/               # sample JSON/HTML for integration tests
│   ├── test_youtube.py         # from test_youtube_transcript.py
│   ├── test_ytdlp.py           # new
│   ├── test_chapters.py        # from test_generate_chapters.py
│   ├── test_html.py            # from test_json_to_html.py
│   ├── test_cli.py             # new — routing logic, argument parsing
│   └── test_transcribe.py      # new
└── ...
```

## Installation

```bash
# Development
uv sync --dev

# Run directly
uv run podcast-reader <url-or-file> [title]

# Install as tool (standalone use)
uv tool install .
podcast-reader <url-or-file> [title]
```

## CLI Interface

```
podcast-reader <url-or-file> [title]
```

Options:
- `--output-dir` — directory for output files (default: current working directory)
- `--model` — Claude model for chapter generation (default: `claude-haiku-4-5-20251001`)

Entry point defined in `pyproject.toml`:
```toml
[project.scripts]
podcast-reader = "podcast_reader.cli:main"
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3` | Whisper model size |
| `WHISPER_LANG` | `en` | Language code |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `HF_TOKEN` | _(none)_ | HuggingFace token for diarization |
| `ANTHROPIC_API_KEY` | _(none)_ | Enables chapter generation via Claude |
| `SENTENCES` | `5` | Sentences per paragraph in HTML |
| `YT_DLP_COOKIES` | _(none)_ | Path to cookies file for yt-dlp (for authenticated content) |

## URL Routing

The CLI determines the transcription method based on the input:

1. **YouTube URL** (youtube.com, youtu.be) → `youtube.py` — fetch existing captions via youtube-transcript-api. No download.
2. **Any other URL** (http/https) → `ytdlp.py` — extract audio via yt-dlp, then whisper. This handles X/Twitter, Vimeo, TikTok, direct audio URLs, and anything else yt-dlp supports.
3. **Local file** → whisper directly.

Note: the previous separate "direct audio URL" route is removed. yt-dlp handles direct audio URLs natively, so there is no need for a separate `urllib`/`curl` download path.

## Module Specifications

### `cli.py` — Main Entry Point

Replaces all orchestration logic from `transcribe.sh`.

**Responsibilities:**
- Parse arguments (url-or-file, optional title, --output-dir, --model) using argparse
- Read environment variables for config
- Route input to the correct handler based on URL detection
- Orchestrate the pipeline: transcript → chapters → HTML
- Cache: skip steps if output files already exist (same behavior as shell script)
- Print status messages and output file paths
- Detect WSL and print Windows-compatible paths when available

**URL detection:**
- YouTube: regex matching `youtube.com/`, `youtu.be/`
- Other URL: any `http://` or `https://` URL not matching YouTube
- Local file: everything else

**Caching and output naming:**
All output files are derived from a "stem" that depends on the input type:
- YouTube: `<video_id>` (e.g., `dQw4w9WgXcQ`)
- yt-dlp: `<platform_id>` from the downloaded audio filename (e.g., `1234567890`)
- Local file: `<filename_without_extension>` (e.g., `episode`)

Output files: `<stem>.json`, `<stem>_chapters.json`, `<stem>.html`, all written to `--output-dir`. Each pipeline step checks if its output file exists and skips if so (print a message telling the user to delete to regenerate).

### `youtube.py` — YouTube Captions

Moved from `youtube_transcript.py`. The `main()` CLI entry point is removed since orchestration moves to `cli.py`. The `.title` sidecar file is no longer written; `cli.py` calls `fetch_video_title()` directly in-process.

**Public API:**
- `extract_video_id(url: str) -> str | None`
- `fetch_transcript(video_id: str) -> list[dict]`
- `snippets_to_whisper_segments(snippets: list[dict]) -> dict`
- `fetch_video_title(video_id: str) -> str` — uses YouTube's oembed endpoint via `urllib.request` (lightweight metadata fetch, not audio download)

### `ytdlp.py` — yt-dlp Download (New)

Downloads audio from any yt-dlp-supported platform (X/Twitter, Vimeo, TikTok, direct audio URLs, etc.).

**Public API:**
- `download_audio(url: str, output_dir: Path, cookies: Path | None = None) -> Path` — extracts audio as mp3, returns path to the file
- `fetch_title(url: str) -> str` — returns the video/post title

**Output naming:** Uses yt-dlp output template `%(id)s.%(ext)s` to produce short, filesystem-safe filenames based on the platform's video ID (e.g., `1234567890.mp3`). This avoids long titles in filenames and replaces the old shell script's 80-character truncation logic. The video ID is always short and unique per platform. If yt-dlp cannot determine an ID, it falls back to a sanitized title.

**Implementation:**
- Uses `subprocess.run` to call yt-dlp CLI
- `download_audio`: runs `yt-dlp -x --audio-format mp3 -o "%(id)s.%(ext)s" <url>`
- `fetch_title`: runs `yt-dlp --print title <url>`
- If `cookies` is provided, passes `--cookies <path>` to yt-dlp
- Raises `RuntimeError` on yt-dlp failure with stderr output; authentication-related failures produce a message suggesting cookie configuration

### `transcribe.py` — Whisper Orchestration

Encapsulates the whisper-ctranslate2 invocation currently done in `transcribe.sh`.

**Public API:**
- `transcribe(audio_path: Path, output_dir: Path, model: str, lang: str, device: str, hf_token: str | None = None) -> Path` — runs whisper-ctranslate2, returns path to JSON output

**Output naming:** The JSON output uses the audio file's stem: `<audio_stem>.json`. For example, `1234567890.mp3` produces `1234567890.json`. This is the same convention as the original shell script (`${FILENAME%.*}.json`).

**Implementation:**
- Uses `subprocess.run` to call `whisper-ctranslate2` CLI
- Constructs args from parameters (model, language, device, hf_token)
- Passes `--output_format json --output_dir <output_dir>` to whisper

### `chapters.py` — Chapter Generation

Moved from `generate_chapters.py`. The `main()` CLI entry point is removed.

**Public API:**
- `format_transcript(segments: list[dict]) -> str`
- `generate_chapters(transcript_text: str, model: str = "claude-haiku-4-5-20251001") -> list[dict]`
- `snap_chapters_to_segments(chapters: list[dict], segments: list[dict]) -> list[dict]`

### `html.py` — HTML Output

Moved from `json_to_html.py`. The `main()` CLI entry point is removed.

**Public API:**
- `build_html(segments: list[dict], title: str, chapters: list[dict] | None = None, sentences_per_para: int = 5, source: str = "whisper-ctranslate2") -> str`
- All helper functions (segments_to_paragraphs, build_sidebar_nav, etc.)

## Dependencies (`pyproject.toml`)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "podcast-reader"
requires-python = ">=3.10"
dependencies = [
    "anthropic>=0.40",
    "youtube-transcript-api>=1.0",
    "yt-dlp>=2024.1.1",
]

[project.optional-dependencies]
whisper = [
    "whisper-ctranslate2>=0.5.7",
]
diarization = [
    "pyannote.audio>=4.0",
]
dev = [
    "pytest>=7.0",
    "mypy>=1.10",
    "ruff>=0.4",
]

[project.scripts]
podcast-reader = "podcast_reader.cli:main"
```

Note: `whisper-ctranslate2` and `pyannote.audio` are optional extras because they have heavy CUDA/torch transitive dependencies. Users who only use YouTube captions or yt-dlp-to-whisper flows on a machine with whisper already installed don't need these in the package.

## Code Quality Standards

### Type Checking

- **Tool:** mypy (strict mode)
- All functions have full type annotations (parameters and return types)
- No `Any` types unless absolutely necessary
- `pyproject.toml` configures mypy:
  ```toml
  [tool.mypy]
  strict = true
  ```

### Formatting & Linting

- **Tool:** ruff (format + lint)
- `pyproject.toml` configures ruff:
  ```toml
  [tool.ruff]
  target-version = "py310"
  line-length = 100

  [tool.ruff.lint]
  select = ["E", "F", "W", "I", "N", "UP", "B", "A", "SIM", "TCH"]
  ```

### Testing Strategy

**Unit tests** — functional style with equality matchers:
- Pure functions tested with exact input → exact output assertions (`assert result == expected`)
- No shape testing (no `assert isinstance(...)`, no `assert len(...) > 0`)
- No mocking of internal code; subprocess calls are the only thing mocked (since they invoke external CLI tools)
- Each module gets its own test file

**Integration tests** — with sample downloaded output:
- `tests/fixtures/` contains real sample outputs: whisper JSON, chapters JSON, HTML
- Integration tests exercise the full pipeline using fixture data
- Example: load a fixture whisper JSON → run `generate_chapters` logic (with a fixture chapters JSON, not calling Claude) → run `html.py` → assert HTML output matches expected fixture
- yt-dlp integration test: download a small, stable public video, verify audio file is produced (marked with `pytest.mark.integration` so it can be skipped in CI)

**Test organization:**
- `tests/test_youtube.py` — moved from `test_youtube_transcript.py`, imports updated
- `tests/test_html.py` — moved from `test_json_to_html.py`, imports updated
- `tests/test_chapters.py` — moved from `test_generate_chapters.py`, imports updated
- `tests/test_ytdlp.py` — new, unit tests for argument construction + integration test for real download
- `tests/test_cli.py` — new, URL routing logic, argument parsing
- `tests/test_transcribe.py` — new, argument construction for whisper CLI

**Pytest configuration** in `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "integration: tests that download from the internet or require external tools",
]
```

**Running tests:**
```bash
# Unit tests only (fast)
uv run pytest -m "not integration"

# All tests including integration
uv run pytest
```

## Pipeline Flow

```
Input URL/file
    │
    ├─ YouTube URL ──────────► youtube.py ──► whisper JSON
    ├─ Any other URL ────────► ytdlp.py (extract audio) ──► whisper-ctranslate2 ──► whisper JSON
    └─ Local file ───────────► whisper-ctranslate2 ──► whisper JSON
                                                              │
                                              chapters.py: generate_chapters() then snap_chapters_to_segments()
                                              (only if ANTHROPIC_API_KEY set)
                                                              │
                                                          html.py ──► styled HTML
```

## Files Changed

| File | Action |
|------|--------|
| `pyproject.toml` | Create — deps, entry point, tool config |
| `src/podcast_reader/__init__.py` | Create — package init |
| `src/podcast_reader/cli.py` | Create — main entry point |
| `src/podcast_reader/transcribe.py` | Create — whisper orchestration |
| `src/podcast_reader/youtube.py` | Create — moved from `youtube_transcript.py` |
| `src/podcast_reader/ytdlp.py` | Create — yt-dlp integration |
| `src/podcast_reader/chapters.py` | Create — moved from `generate_chapters.py` |
| `src/podcast_reader/html.py` | Create — moved from `json_to_html.py` |
| `tests/__init__.py` | Keep |
| `tests/fixtures/` | Create — sample JSON/HTML for integration tests |
| `tests/test_youtube.py` | Move from `test_youtube_transcript.py`, update imports |
| `tests/test_html.py` | Move from `test_json_to_html.py`, update imports |
| `tests/test_chapters.py` | Move from `test_generate_chapters.py`, update imports |
| `tests/test_ytdlp.py` | Create — yt-dlp tests |
| `tests/test_cli.py` | Create — CLI routing + arg parsing tests |
| `tests/test_transcribe.py` | Create — whisper orchestration tests |
| `transcribe.sh` | Delete |
| `youtube_transcript.py` | Delete (moved) |
| `generate_chapters.py` | Delete (moved) |
| `json_to_html.py` | Delete (moved) |
| `requirements.txt` | Delete (replaced by pyproject.toml) |
| `CLAUDE.md` | Update — new structure, CLI usage, code quality tools |

## What's NOT Changing

- HTML output format and styling
- Chapter generation logic and prompts
- YouTube caption fetching behavior
- Whisper transcription parameters
- Caching behavior (skip if output exists)
