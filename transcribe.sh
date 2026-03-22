#!/usr/bin/env bash
# Transcribe a podcast URL or local audio file to a styled HTML transcript.
#
# Usage:
#   ./transcribe.sh <url-or-file> [title]
#
# Examples:
#   ./transcribe.sh https://example.com/episode.mp3 "My Podcast Ep 1"
#   ./transcribe.sh ~/Downloads/episode.mp3
#   ./transcribe.sh recording.wav "Interview with Alice"
#
# Options (via environment variables):
#   WHISPER_MODEL   - Model size (default: large-v3)
#   WHISPER_LANG    - Language code (default: en)
#   WHISPER_DEVICE  - cuda or cpu (default: cuda)
#   HF_TOKEN        - HuggingFace token for speaker diarization (optional)
#   SENTENCES       - Sentences per paragraph in HTML output (default: 5)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Config ---
MODEL="${WHISPER_MODEL:-large-v3}"
LANG="${WHISPER_LANG:-en}"
DEVICE="${WHISPER_DEVICE:-cuda}"
SENTENCES="${SENTENCES:-5}"

# --- Args ---
if [ $# -lt 1 ]; then
    echo "Usage: $0 <url-or-file> [title]" >&2
    exit 1
fi

INPUT="$1"
TITLE="${2:-}"

# --- Activate venv ---
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "Creating virtual environment..."
    uv venv "$SCRIPT_DIR/.venv"
    source "$SCRIPT_DIR/.venv/bin/activate"
    uv pip install -r "$SCRIPT_DIR/requirements.txt"
else
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# --- Resolve input to a local file ---
if [[ "$INPUT" =~ ^https?:// ]]; then
    FILENAME=$(basename "$INPUT" | sed 's/\?.*//')
    # Use a sanitized name if the URL filename is too long or ugly
    if [ ${#FILENAME} -gt 80 ]; then
        FILENAME="podcast_$(date +%Y%m%d_%H%M%S).mp3"
    fi
    AUDIO_PATH="$SCRIPT_DIR/$FILENAME"
    if [ -f "$AUDIO_PATH" ]; then
        echo "Audio already downloaded: $AUDIO_PATH"
    else
        echo "Downloading: $INPUT"
        curl -L -o "$AUDIO_PATH" "$INPUT"
    fi
else
    AUDIO_PATH="$(realpath "$INPUT")"
    FILENAME="$(basename "$AUDIO_PATH")"
fi

if [ ! -f "$AUDIO_PATH" ]; then
    echo "Error: File not found: $AUDIO_PATH" >&2
    exit 1
fi

STEM="${FILENAME%.*}"
JSON_PATH="$SCRIPT_DIR/${STEM}.json"
HTML_PATH="$SCRIPT_DIR/${STEM}.html"

# --- Transcribe ---
if [ -f "$JSON_PATH" ]; then
    echo "Transcript JSON already exists: $JSON_PATH (delete to re-transcribe)"
else
    echo "Transcribing with whisper-ctranslate2 (model=$MODEL, lang=$LANG, device=$DEVICE)..."

    WHISPER_ARGS=(
        "$AUDIO_PATH"
        --model "$MODEL"
        --language "$LANG"
        --device "$DEVICE"
        --output_format json
        --output_dir "$SCRIPT_DIR"
        --print_colors False
    )

    if [ -n "${HF_TOKEN:-}" ]; then
        echo "HF_TOKEN detected — enabling speaker diarization"
        WHISPER_ARGS+=(--hf_token "$HF_TOKEN")
    fi

    whisper-ctranslate2 "${WHISPER_ARGS[@]}"
fi

# --- Convert to HTML ---
if [ -z "$TITLE" ]; then
    TITLE=$(echo "$STEM" | sed 's/[_-]/ /g' | sed 's/.*/\L&/; s/[a-z]*/\u&/g')
fi

echo "Generating HTML transcript..."
python3 "$SCRIPT_DIR/json_to_html.py" "$JSON_PATH" --title "$TITLE" --sentences "$SENTENCES"

echo ""
echo "Done! Output files:"
echo "  JSON: $JSON_PATH"
echo "  HTML: $HTML_PATH"

# Show Windows path if running in WSL
if command -v wslpath &>/dev/null; then
    WIN_PATH=$(wslpath -w "$HTML_PATH")
    echo "  Windows: $WIN_PATH"
fi
