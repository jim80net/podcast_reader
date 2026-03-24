"""Main CLI entry point for podcast-reader."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from podcast_reader.chapters import (
    format_transcript,
    generate_chapters,
    snap_chapters_to_segments,
)
from podcast_reader.html import build_html
from podcast_reader.transcribe import transcribe
from podcast_reader.youtube import (
    extract_video_id,
    fetch_transcript,
    fetch_video_title,
    snippets_to_whisper_segments,
)
from podcast_reader.ytdlp import download_audio, fetch_title


class InputType(Enum):
    """Classification of the input argument."""

    YOUTUBE = "youtube"
    URL = "url"
    LOCAL_FILE = "local_file"


_YT_URL_RE = re.compile(r"youtube\.com/|youtu\.be/")


def classify_input(input_arg: str) -> InputType:
    """Classify the input as YouTube, generic URL, or local file."""
    if _YT_URL_RE.search(input_arg):
        return InputType.YOUTUBE
    if input_arg.startswith(("http://", "https://")):
        return InputType.URL
    return InputType.LOCAL_FILE


def _wsl_path(path: Path) -> str | None:
    """Convert a Linux path to a Windows path if running in WSL."""
    if shutil.which("wslpath") is None:
        return None
    try:
        result = subprocess.run(
            ["wslpath", "-w", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _transcribe_if_needed(
    *,
    audio_path: Path,
    json_path: Path,
    output_dir: Path,
    whisper_model: str,
    whisper_lang: str,
    whisper_device: str,
    hf_token: str | None,
) -> None:
    """Run whisper transcription if JSON output doesn't already exist."""
    if json_path.exists():
        print(f"Transcript JSON already exists: {json_path} (delete to re-transcribe)")
        return
    print(
        f"Transcribing with whisper-ctranslate2 "
        f"(model={whisper_model}, lang={whisper_lang}, device={whisper_device})..."
    )
    transcribe(
        audio_path=audio_path,
        output_dir=output_dir,
        model=whisper_model,
        lang=whisper_lang,
        device=whisper_device,
        hf_token=hf_token,
    )


def _run_pipeline(
    *,
    input_arg: str,
    title: str | None,
    output_dir: Path,
    model: str,
    whisper_model: str,
    whisper_lang: str,
    whisper_device: str,
    hf_token: str | None,
    sentences: int,
    cookies: Path | None,
) -> None:
    """Run the full transcription pipeline."""
    input_type = classify_input(input_arg)
    stem: str
    json_path: Path
    transcript_source: str

    if input_type == InputType.YOUTUBE:
        video_id = extract_video_id(input_arg)
        if not video_id:
            print(
                f"Error: Could not extract video ID from: {input_arg}",
                file=sys.stderr,
            )
            sys.exit(1)

        stem = video_id
        json_path = output_dir / f"{stem}.json"
        transcript_source = "youtube-captions"

        if title is None:
            title = fetch_video_title(video_id)
            print(f"Video: {title}")

        if json_path.exists():
            print(f"Transcript JSON already exists: {json_path} (delete to re-fetch)")
        else:
            print(f"Fetching transcript for {video_id}...")
            snippets = fetch_transcript(video_id)
            data = snippets_to_whisper_segments(snippets)
            json_path.write_text(json.dumps(data, indent=2))
            print(f"Written {len(data['segments'])} segments to {json_path}")

    elif input_type == InputType.URL:
        if title is None:
            try:
                title = fetch_title(input_arg)
                print(f"Video: {title}")
            except RuntimeError:
                title = None  # will derive from stem later

        # Check if audio was already downloaded by yt-dlp (uses %(id)s template)
        yt_dlp_mp3s = [
            p
            for p in output_dir.glob("*.mp3")
            if not p.stem.startswith("podcast_")  # skip user files
        ]
        audio_path: Path
        if yt_dlp_mp3s:
            audio_path = yt_dlp_mp3s[0]
            print(f"Audio already exists: {audio_path} (delete to re-download)")
        else:
            print("Downloading with yt-dlp...")
            audio_path = download_audio(input_arg, output_dir, cookies=cookies)
        stem = audio_path.stem
        json_path = output_dir / f"{stem}.json"
        transcript_source = "whisper-ctranslate2"

        _transcribe_if_needed(
            audio_path=audio_path,
            json_path=json_path,
            output_dir=output_dir,
            whisper_model=whisper_model,
            whisper_lang=whisper_lang,
            whisper_device=whisper_device,
            hf_token=hf_token,
        )

    else:
        audio_path = Path(input_arg).resolve()
        if not audio_path.exists():
            print(
                f"Error: File not found: {audio_path}",
                file=sys.stderr,
            )
            sys.exit(1)

        stem = audio_path.stem
        json_path = output_dir / f"{stem}.json"
        transcript_source = "whisper-ctranslate2"

        _transcribe_if_needed(
            audio_path=audio_path,
            json_path=json_path,
            output_dir=output_dir,
            whisper_model=whisper_model,
            whisper_lang=whisper_lang,
            whisper_device=whisper_device,
            hf_token=hf_token,
        )

    # --- Generate chapters (optional) ---
    chapters_path = output_dir / f"{stem}_chapters.json"
    chapters: list[dict[str, Any]] | None = None

    if chapters_path.exists():
        print(f"Chapters JSON already exists: {chapters_path} (delete to regenerate)")
        chapters = json.loads(chapters_path.read_text())
    elif os.environ.get("ANTHROPIC_API_KEY"):
        print("Generating chapter markers with Claude...")
        data = json.loads(json_path.read_text())
        segments = [s for s in data["segments"] if s.get("text", "").strip()]
        transcript_text = format_transcript(segments)
        chapters = generate_chapters(transcript_text, model=model)
        chapters = snap_chapters_to_segments(chapters, segments)
        chapters_path.write_text(json.dumps(chapters, indent=2))
        print(f"Written {len(chapters)} chapters to {chapters_path}")
    else:
        print("Skipping chapter generation (set ANTHROPIC_API_KEY to enable)")

    # --- Convert to HTML ---
    if title is None:
        title = stem.replace("_", " ").replace("-", " ").title()

    html_path = output_dir / f"{stem}.html"
    data = json.loads(json_path.read_text())
    segments = [s for s in data["segments"] if s.get("text", "").strip()]

    print("Generating HTML transcript...")
    html_content = build_html(
        segments,
        title,
        chapters=chapters,
        sentences_per_para=sentences,
        source=transcript_source,
    )
    html_path.write_text(html_content)

    print()
    print("Done! Output files:")
    print(f"  JSON: {json_path}")
    if chapters is not None:
        print(f"  Chapters: {chapters_path}")
    print(f"  HTML: {html_path}")

    win_path = _wsl_path(html_path)
    if win_path:
        print(f"  Windows: {win_path}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="podcast-reader",
        description=("Transcribe podcast audio or YouTube/X videos to styled HTML transcripts"),
    )
    parser.add_argument("input", help="URL or local file path")
    parser.add_argument(
        "title",
        nargs="?",
        default=None,
        help="Document title (optional)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help=("Claude model for chapters (default: claude-haiku-4-5-20251001)"),
    )
    args = parser.parse_args()

    cookies_env = os.environ.get("YT_DLP_COOKIES")
    cookies = Path(cookies_env) if cookies_env else None

    _run_pipeline(
        input_arg=args.input,
        title=args.title,
        output_dir=args.output_dir,
        model=args.model,
        whisper_model=os.environ.get("WHISPER_MODEL", "large-v3"),
        whisper_lang=os.environ.get("WHISPER_LANG", "en"),
        whisper_device=os.environ.get("WHISPER_DEVICE", "cuda"),
        hf_token=os.environ.get("HF_TOKEN"),
        sentences=int(os.environ.get("SENTENCES", "5")),
        cookies=cookies,
    )
