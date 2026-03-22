# Podcast Reader

Transcribe podcast audio files into readable, styled HTML transcripts with timestamps.

Uses [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) — a fast, GPU-accelerated Whisper implementation (4x faster than OpenAI's Whisper).

## Usage

```bash
./transcribe.sh <url-or-file> [title]
```

### Examples

```bash
# From a URL (downloads automatically)
./transcribe.sh https://media.rss.com/show/episode.mp3 "Episode 42: The Answer"

# From a local file
./transcribe.sh ~/Downloads/interview.mp3 "Interview with Dr. Smith"

# With speaker diarization
HF_TOKEN=hf_xxx ./transcribe.sh episode.mp3 "Panel Discussion"

# With chapter generation (requires Anthropic API key)
ANTHROPIC_API_KEY=sk-ant-xxx ./transcribe.sh episode.mp3 "Episode 42"

# Customize model and paragraph size
WHISPER_MODEL=medium SENTENCES=3 ./transcribe.sh episode.mp3
```

### Output

The script produces:
- `<name>.json` — Raw Whisper segments with timestamps
- `<name>_chapters.json` — Chapter markers with titles, abstracts, and type tags (if `ANTHROPIC_API_KEY` is set)
- `<name>.html` — Styled, readable transcript with timestamp badges

When chapters are generated, the HTML includes:
- **Table of contents** with chapter titles, timestamps, and abstracts
- **Chapter sections** with headings and summaries
- **Sponsor dimming** — sponsor/ad segments are visually muted (hover to reveal)
- **Anchor navigation** — click any TOC entry to jump to that section

The HTML supports both dark and light themes automatically via `prefers-color-scheme`.

Open in a browser from WSL:
```
\\wsl.localhost\Ubuntu\<path>\<name>.html
```

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) for Python package management
- NVIDIA GPU recommended (falls back to CPU)
- `ffmpeg` (usually pre-installed)

## Setup

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Or just run `./transcribe.sh` — it bootstraps the venv automatically on first run.
