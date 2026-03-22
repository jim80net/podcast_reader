# Podcast Reader

Transcribe podcast audio files or YouTube videos into readable, styled HTML transcripts with timestamps.

Uses [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) for audio files (GPU-accelerated, 4x faster than OpenAI's Whisper) and [youtube-transcript-api](https://pypi.org/project/youtube-transcript-api/) for YouTube videos (fetches existing captions — no audio download needed).

<img width="1899" height="1447" alt="image" src="https://github.com/user-attachments/assets/de666976-cfd4-4a3b-84a3-653c8fade903" />


## Usage

```bash
./transcribe.sh <url-or-file> [title]
```

### Examples

```bash
# From a YouTube video (uses existing captions, no download)
./transcribe.sh https://www.youtube.com/watch?v=VIDEO_ID "Episode Title"

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
- `<name>.json` — Transcript segments with timestamps (from Whisper or YouTube captions)
- `<name>_chapters.json` — Chapter markers with titles, abstracts, key points, pull quotes, and type tags (if `ANTHROPIC_API_KEY` is set)
- `<name>.html` — Styled, readable transcript with timestamp badges

For YouTube videos, `<name>` is the video ID (e.g., `fkKh_WBT5BM.json`). The title is auto-extracted from YouTube if not provided.

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
- For audio transcription: NVIDIA GPU recommended (falls back to CPU), `ffmpeg`
- For YouTube: no additional requirements (captions are fetched directly)

## Setup

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Or just run `./transcribe.sh` — it bootstraps the venv automatically on first run.
