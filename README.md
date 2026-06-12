# Podcast Reader

Transcribe podcast audio files, YouTube videos, or X/Twitter videos into readable, styled HTML transcripts with timestamps.

Uses [youtube-transcript-api](https://pypi.org/project/youtube-transcript-api/) for YouTube videos (fetches existing captions — no audio download needed), [yt-dlp](https://github.com/yt-dlp/yt-dlp) to download audio from X/Twitter and other platforms, and [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) for audio transcription (GPU-accelerated, 4x faster than OpenAI's Whisper).

<img width="1899" height="1447" alt="image" src="https://github.com/user-attachments/assets/de666976-cfd4-4a3b-84a3-653c8fade903" />

## Usage

```bash
podcast-reader <url-or-file> [title] [--output-dir DIR] [--provider PROVIDER] [--model MODEL]
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

# With chapter generation (bring your own API key; Anthropic is the default provider)
ANTHROPIC_API_KEY=sk-ant-xxx podcast-reader episode.mp3 "Episode 42"

# Chapter generation via another provider (see the provider table below)
DEEPSEEK_API_KEY=sk-xxx podcast-reader --provider deepseek episode.mp3 "Episode 42"

# Customize whisper model and paragraph size
WHISPER_MODEL=medium SENTENCES=3 podcast-reader episode.mp3

# Write outputs somewhere other than the current directory
podcast-reader --output-dir ./output https://example.com/video
```

If the title is omitted, it is auto-extracted from YouTube or via yt-dlp where possible.

### Engine mode (for apps and integrations)

```bash
podcast-reader serve [--discovery-file PATH]
```

Starts a localhost-only HTTP engine (FastAPI) exposing the same pipeline as a job
API: `POST /v1/jobs`, `GET /v1/jobs/{id}`, SSE progress at `GET /v1/events`, a
managed transcript library (`~/PodcastReader/` by default), and `GET /v1/health`.
All endpoints require the bearer token the engine generates on first start
(`engine-state.json` in the data directory); the port is fixed per install and
advertised in a discovery file. Token and discovery files are written with
owner-only permissions (0600) on POSIX; on Windows, where POSIX mode bits are
a no-op, they are protected by the user-profile directory ACLs instead. This
is the foundation for the desktop app — see
`docs/superpowers/specs/2026-06-11-desktop-packaging-design.md`.

### Desktop app (`app/`)

An Electron shell for the engine lives in [`app/`](app/README.md): submit
jobs, watch step-level progress live, read transcripts, and manage
settings/API keys — all over the engine's authenticated `/v1` API (the
renderer itself is credential-free). It registers the `podcast-reader://`
protocol, and protocol-initiated jobs always wait for an explicit
confirmation click before running.

```bash
cd app
npm install
npm run dev          # dev posture: spawns `uv run podcast-reader serve` for you
```

**Engine posture:** the app finds its engine via, in order: a packaged
`resources/engine/` payload (built with `packaging/build_engine.py`, see
below), the `PODCAST_READER_ENGINE_CMD` env override (plain whitespace
split — no paths with spaces), or `uv run podcast-reader serve` from the
repo root — so the repo's Python toolchain (`uv sync --extra dev`) is
assumed for development.

**First run & packs:** a packaged app cannot acquire Python packages after
install, so heavyweight runtime pieces are downloadable *packs* managed by
the engine (`GET/POST/DELETE /v1/packs`). On first run a setup wizard
detects hardware, pre-checks the recommended packs, and downloads them with
live progress (resumable; re-runnable from Settings → "Run setup again").
Settings → Packs installs/uninstalls each pack and shows license
attributions. Approximate download sizes:

| Pack | Download | Notes |
|------|----------|-------|
| Whisper model `tiny` | 78 MB | CI / low-end |
| Whisper model `small` | 486 MB | CPU default |
| Whisper model `medium` | 1.5 GB | |
| Whisper model `large-v3` | 3.1 GB | recommended with an NVIDIA GPU |
| NVIDIA CUDA runtime (cuBLAS + cuDNN 9) | 1.2 GB | Windows + NVIDIA only; from NVIDIA's official PyPI wheels |
| Speaker diarization worker | ~340 MB | not yet published — shows `unavailable` |

**Unsigned builds:** installers built today are unsigned dev artifacts
(`npm run dist -- --win` / `-- --mac`): Windows SmartScreen needs
"Run anyway", macOS needs right-click → Open (and macOS auto-update does
not work unsigned). Signed/notarized release pipelines are gated on
code-signing credentials. Details in [`app/README.md`](app/README.md).

### Frozen engine builds (`packaging/`)

The production engine ships as a PyInstaller onedir (engine +
`whisper-worker` sharing one `_internal/`, with yt-dlp/ffmpeg/ffprobe seeds
baked into `_internal/tools/`):

```bash
cd packaging
uv venv .venv-engine --python 3.10
uv pip install --python .venv-engine/bin/python '..[worker]' pyinstaller
.venv-engine/bin/python build_engine.py            # → packaging/dist/engine/
python3 frozen_smoke.py dist/engine/podcast-reader-engine   # e2e proof
cd ../app && npm run dist -- --engine-dir ../packaging/dist/engine --win
```

CI (`frozen-smoke`) builds this engine on ubuntu + windows and proves it
end-to-end: boot, authenticated handshake, `POST /v1/packs/model-tiny/install`,
and a fixture-WAV transcription through the frozen worker. The diarization
worker pack has its own build (`build_diarization_pack.py`, requires
`HF_TOKEN` with accepted `pyannote/speaker-diarization-community-1` terms).

### Output

The pipeline produces (in `--output-dir`, default: current directory):

- `<name>.json` — Transcript segments with timestamps (from Whisper or YouTube captions)
- `<name>_chapters.json` — Chapter markers with titles, abstracts, key points, pull quotes, and type tags (if an API key for the selected chapter provider is available)
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
# Install as a standalone tool with audio transcription support
uv tool install '.[whisper]'

# Or run from the repo without installing
uv run podcast-reader <url-or-file> [title]
```

A bare `uv tool install .` works for YouTube URLs (captions only); transcribing local files or non-YouTube URLs requires the `whisper` extra. Chapter generation is built in — just provide an API key for your provider (see [Chapter providers](#chapter-providers)).

Optional features are packaged as extras:

| Extra | Enables | Pulls in |
|-------|---------|----------|
| `whisper` | Transcribing audio files and non-YouTube URLs | `whisper-ctranslate2` |
| `chapters` | _(empty compatibility alias — chapters are now built in)_ | — |
| `diarization` | Speaker labels | `pyannote.audio` |
| `dev` | Tests, type checking, linting | `pytest`, `mypy`, `ruff` |

```bash
# Example: development setup
uv sync --extra dev

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
| `SENTENCES` | `5` | Sentences per paragraph in HTML |
| `YT_DLP_COOKIES` | _(none)_ | Path to cookies file for authenticated yt-dlp downloads |
| `PODCAST_READER_DATA_DIR` | `~/PodcastReader` | Engine data directory (library, job journal, settings) |
| `PODCAST_READER_TOOLS_DIR` | _(none)_ | Preferred directory for external tools (yt-dlp, ffmpeg) |

### Chapter providers

Chapter generation works with any provider from the registry — select one with
`--provider` (default: `anthropic`) and export that provider's API key. All
providers are reached through the same OpenAI-compatible `/chat/completions`
request. Without a key, the transcript still renders — just without chapters.

| Provider | Key environment variable | Default model |
|----------|--------------------------|---------------|
| `anthropic` _(default)_ | `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` |
| `openai` | `OPENAI_API_KEY` | `gpt-5.4-mini` |
| `xai` | `XAI_API_KEY` | `grok-4.3` |
| `openrouter` | `OPENROUTER_API_KEY` | `anthropic/claude-haiku-4.5` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| `custom` | `PODCAST_READER_CUSTOM_PROVIDER_KEY` | _(set with `--model`)_ |

`--model` overrides the provider's default model. The `custom` provider sends
requests to `PODCAST_READER_CUSTOM_PROVIDER_URL` (must be `https`, or `http`
on localhost — e.g. a local OpenAI-compatible server).

In engine mode, the provider and model are engine settings
(`PUT /v1/settings`: `chapter_provider`, `chapter_model`,
`custom_provider_url`), and API keys are pushed into process memory via
`PUT /v1/keys {provider, api_key}` — they are never written to disk, never
readable back through the API, and are lost on engine restart. Headless
`podcast-reader serve` deployments can keep exporting the provider's key
environment variable instead; a pushed key takes precedence.

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
