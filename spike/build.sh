#!/usr/bin/env bash
# Build the spike onedir bundle. Run from spike/.
#
# Prereqs:
#   uv venv .venv --python 3.10
#   uv pip install --python .venv/bin/python fastapi uvicorn faster-whisper pyinstaller typing_extensions
set -euo pipefail
cd "$(dirname "$0")"
.venv/bin/pyinstaller engine.spec --noconfirm
echo "--- size breakdown ---"
du -sh dist/spike-engine
du -sh dist/spike-engine/_internal/* | sort -rh | head -20
