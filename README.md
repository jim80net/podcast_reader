# Podcast Reader

Transcribe podcast audio files, YouTube videos, or X/Twitter videos into readable, styled HTML transcripts with timestamps.

Uses [youtube-transcript-api](https://pypi.org/project/youtube-transcript-api/) for YouTube videos (fetches existing captions — no audio download needed), [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download audio from X/Twitter and other platforms, and [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) for audio transcription (GPU-accelerated, 4x faster than OpenAI's Whisper).

<img width="1899" height="1447" alt="image" src="https://github.com/user-attachments/assets/de666976-cfd4-4a3b-84a3-653c8fade903" />

## Usage

```bash
podcast-reader <url-or-file> [title] [--output-dir DIR] [--model CLAUDE_MODEL]
```

### Examples

```bash
# From a YouTube video (uses existing captions, no download)
podcast-reader https://www.youtube.com/watch?v=VIDEO_ID "Episode Title"

# From an X/Twitter post (downloads audio via yt-dlp, transcribes with whisper)
podcast-reader https://x.com/user/status/123456 "Post Title"

# From any yt-dlp-supported URL
podcast-reader https://vimeo.com/123456 "Video Title"

# From a local file
podcast-reader ~/Downloads/interview.mp3 "Interview with Dr. Smith"

# With speaker diarization
HF_TOKEN=hf_xxx podcast-reader episode.mp3 "Panel Discussion"

# With chapter generation (requires Anthropic API key)
ANTHROPIC_API_KEY=sk-ant-xxx podcast-reader episode.mp3 "Episode 42"

# Customize whisper model and paragraph size
WHISPER_MODEL=medium SENTENCES=3 podcast-reader episode.mp3

# Write outputs somewhere other than the current directory
podcast-reader --output-dir ./output https://example.com/video
```

If the title is omitted, it is auto-extracted from YouTube or via yt-dlp where possible.

### Output

The pipeline produces (in `--output-dir`, default: current directory):

- `<name>.json` — Transcript segments with timestamps (from Whisper or YouTube captions)
- `<name>_chapters.json` — Chapter markers with titles, abstracts, key points, pull quotes, and type tags (if `ANTHROPIC_API_KEY` is set)
- `<name>.html` — Styled, readable transcript with timestamp badges

For YouTube videos, `<name>` is the video ID (e.g., `fkKh_WBT5BM.json`). For downloaded URLs, it's the audio filename produced by yt-dlp.

Intermediate outputs are cached: re-running the same input skips the download, transcription, and chapter steps if their output files already exist (delete a file to regenerate it).

When chapters are generated, the HTML includes:

- **Table of contents** with chapter titles, timestamps, and abstracts
- **Chapter sections** with headings and summaries
- **Key points** — bullet-point summaries in a sticky right gutter (hidden on narrow screens)
- **Pull quotes** — standout phrases rendered as bold inline text after each chapter abstract
- **Sponsor dimming** — sponsor/ad segments are visually muted (hover to reveal)
- **Anchor navigation** — click any TOC entry to jump to that section

The HTML supports both dark and light themes automatically via `prefers-color-scheme`.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) for Python package management
- For audio transcription: NVIDIA GPU recommended (set `WHISPER_DEVICE=cpu` to fall back), `ffmpeg` (used by yt-dlp for audio extraction)
- For YouTube: no additional requirements (captions are fetched directly)

## Setup

```bash
# Install as a standalone tool with audio transcription and chapter support
uv tool install '.[whisper,chapters]'

# Or run from the repo without installing
uv run podcast-reader <url-or-file> [title]
```

A bare `uv tool install .` works for YouTube URLs (captions only); transcribing local files or non-YouTube URLs requires the `whisper` extra.

Optional features are packaged as extras:

| Extra | Enables | Pulls in |
|-------|---------|----------|
| `whisper` | Transcribing audio files and non-YouTube URLs | `whisper-ctranslate2` |
| `chapters` | Chapter generation via Claude | `anthropic` |
| `diarization` | Speaker labels | `pyannote.audio` |
| `dev` | Tests, type checking, linting | `pytest`, `mypy`, `ruff`, `anthropic` |

```bash
# Example: development setup with chapter generation
uv sync --extra dev --extra chapters

# Example: everything needed to transcribe local audio with speaker labels
uv sync --extra whisper --extra diarization
```

For speaker diarization, set `HF_TOKEN` and accept the model terms at:

- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3` | Whisper model size |
| `WHISPER_LANG` | `en` | Language code |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `HF_TOKEN` | _(none)_ | HuggingFace token, enables speaker diarization |
| `ANTHROPIC_API_KEY` | _(none)_ | Enables chapter generation via Claude |
| `SENTENCES` | `5` | Sentences per paragraph in HTML |
| `YT_DLP_COOKIES` | _(none)_ | Path to cookies file for authenticated yt-dlp downloads |

The Claude model used for chapters defaults to `claude-haiku-4-5-20251001` and can be overridden with `--model`.

## Development

See [CLAUDE.md](CLAUDE.md) for the package structure and pipeline details.

```bash
# Run tests (unit only)
uv run pytest -m "not integration"

# Run all tests including integration
uv run pytest

# Type checking (strict mode)
uv run mypy src/

# Lint and format
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```
