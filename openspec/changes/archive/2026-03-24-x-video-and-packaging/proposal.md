## Why

The project started as a shell script (`transcribe.sh`) plus ad-hoc Python helpers. To support X/Twitter videos (and any other platform), yt-dlp was required. Distributing a shell script + multiple Python files was fragile. The team wanted a proper, installable Python CLI package with:

- First-class X/Twitter (and generic yt-dlp) support
- Strict typing, linting, and test coverage
- Easy installation via `uv tool install` or pip
- Clean module boundaries

## What Changes

- **BREAKING (for previous shell users):** Entry point changed from `./transcribe.sh` to `podcast-reader` CLI (or `uv run podcast-reader`).
- New `yt-dlp` integration for audio extraction from any supported URL (X, Vimeo, TikTok, direct audio, etc.).
- Full Python package under `src/podcast_reader/` with six modules: cli, youtube, ytdlp, transcribe, chapters, html.
- All code strictly typed (mypy --strict), formatted and linted with ruff, tested with pytest (unit + integration markers).
- pyproject.toml replaces requirements.txt; hatchling build backend.
- Old shell script and loose Python files removed (moved into the package).
- CLI now classifies input (YouTube vs generic URL vs local file) and orchestrates the full pipeline in-process.

## Capabilities

### New Capabilities
- `python-cli-packaging`: Distributable `podcast-reader` CLI via uv/pip with console script entry point.
- `yt-dlp-audio-extraction`: Download/extract audio from X/Twitter and any yt-dlp-supported source as mp3.
- `x-twitter-video-support`: Transcribe X/Twitter status URLs end-to-end using the yt-dlp path.
- `strict-code-quality`: mypy strict, ruff, pytest with clear unit/integration separation enforced in CI and developer workflow.

### Modified Capabilities
- `youtube-captions`: YouTube path was moved from standalone script into the `podcast_reader.youtube` module (internal refactor, no behavior change for users).

## Impact

- New core dependency: `yt-dlp`
- Optional extras for heavy whisper and diarization backends
- Developer experience change: `uv sync --extra dev` instead of raw pip/venv
- All previous loose files (`transcribe.sh`, `youtube_transcript.py`, `generate_chapters.py`, `json_to_html.py`, `requirements.txt`) deleted or migrated
- New test layout under `tests/`
- HTML, chapter logic, and YouTube caption behavior preserved
