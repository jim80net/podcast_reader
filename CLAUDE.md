# podcast_reader

Transcribe podcast audio to styled HTML transcripts using [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) (faster-whisper + CLI).

## Quick Start

```bash
# Transcribe from a URL
./transcribe.sh https://example.com/episode.mp3 "Episode Title"

# Transcribe a local file
./transcribe.sh ~/Downloads/episode.mp3 "Episode Title"
```

## Setup

Requires: Python 3.10+, `uv`, NVIDIA GPU (optional, falls back to CPU).

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

For speaker diarization, set `HF_TOKEN` and accept model terms at:
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3` | Whisper model size |
| `WHISPER_LANG` | `en` | Language code |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `HF_TOKEN` | _(none)_ | HuggingFace token for diarization |
| `SENTENCES` | `5` | Sentences per paragraph in HTML |

## Files

| File | Purpose |
|------|---------|
| `transcribe.sh` | Main entry point — download, transcribe, convert |
| `json_to_html.py` | Convert whisper JSON to styled HTML (reusable standalone) |
| `requirements.txt` | Python dependencies |

## Development

- Use `uv` for all Python package management, never raw `pip`.
- Audio files, JSON, and HTML outputs are gitignored — they're generated artifacts.
