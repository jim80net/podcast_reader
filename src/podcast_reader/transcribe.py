"""Orchestrate whisper-ctranslate2 transcription."""

from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003 — used at runtime in path operations


def build_whisper_args(
    audio_path: Path,
    output_dir: Path,
    model: str,
    lang: str,
    device: str,
    hf_token: str | None = None,
) -> list[str]:
    """Build the whisper-ctranslate2 command-line arguments."""
    args = [
        "whisper-ctranslate2",
        str(audio_path),
        "--model",
        model,
        "--language",
        lang,
        "--device",
        device,
        "--output_format",
        "json",
        "--output_dir",
        str(output_dir),
        "--print_colors",
        "False",
    ]
    if hf_token is not None:
        args.extend(["--hf_token", hf_token])
    return args


def transcribe(
    audio_path: Path,
    output_dir: Path,
    model: str,
    lang: str,
    device: str,
    hf_token: str | None = None,
) -> Path:
    """Run whisper-ctranslate2 on an audio file and return the path to the JSON output."""
    args = build_whisper_args(audio_path, output_dir, model, lang, device, hf_token)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"whisper-ctranslate2 failed: {result.stderr.strip()}")

    json_path = output_dir / f"{audio_path.stem}.json"
    return json_path
