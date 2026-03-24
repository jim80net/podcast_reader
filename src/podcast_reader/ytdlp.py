"""Download audio from any yt-dlp-supported platform."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def build_download_args(url: str, output_dir: Path, cookies: Path | None = None) -> list[str]:
    """Build the yt-dlp command-line arguments for audio extraction."""
    args = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
    ]
    if cookies is not None:
        args.extend(["--cookies", str(cookies)])
    args.extend(["-o", f"{output_dir}/%(id)s.%(ext)s", url])
    return args


def build_title_args(url: str) -> list[str]:
    """Build the yt-dlp command-line arguments for title extraction."""
    return ["yt-dlp", "--print", "title", url]


def fetch_title(url: str) -> str:
    """Fetch the video/post title using yt-dlp."""
    result = subprocess.run(
        build_title_args(url),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed to fetch title: {result.stderr.strip()}")
    return result.stdout.strip()


def download_audio(url: str, output_dir: Path, cookies: Path | None = None) -> Path:
    """Download and extract audio as mp3 from a URL.

    Returns the path to the downloaded mp3 file.
    """
    args = build_download_args(url, output_dir, cookies)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"yt-dlp failed: {stderr}"
        if "login" in stderr.lower() or "auth" in stderr.lower():
            msg += "\nHint: set YT_DLP_COOKIES to a cookies file path for authenticated content."
        raise RuntimeError(msg)

    # Find the downloaded mp3 file
    mp3_files = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp3_files:
        raise RuntimeError(f"yt-dlp completed but no mp3 file found in {output_dir}")
    return mp3_files[0]
