"""Fetch YouTube captions and output whisper-compatible JSON."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi

_YT_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})"),
]


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from a URL. Returns None if not a YouTube URL."""
    for pattern in _YT_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def snippets_to_whisper_segments(snippets: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert youtube-transcript-api snippets to whisper-ctranslate2 JSON format."""
    segments: list[dict[str, Any]] = []
    for s in snippets:
        text = s["text"].strip()
        if not text:
            continue
        segments.append(
            {
                "start": s["start"],
                "end": s["start"] + s["duration"],
                "text": text,
            }
        )
    return {"segments": segments}


def fetch_transcript(video_id: str) -> list[dict[str, Any]]:
    """Fetch transcript for a YouTube video. Prefers manual captions over auto-generated."""
    from youtube_transcript_api import NoTranscriptFound

    ytt_api = YouTubeTranscriptApi()
    transcript_list = ytt_api.list(video_id)

    try:
        transcript = transcript_list.find_transcript(["en"])
    except NoTranscriptFound as exc:
        raise SystemExit(f"Error: No English transcript available for {video_id}") from exc

    fetched = transcript.fetch()
    return fetched.to_raw_data()


def fetch_video_title(video_id: str) -> str:
    """Fetch the video title from YouTube's oembed endpoint."""
    url = (
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("title", video_id)  # type: ignore[no-any-return]
    except Exception:
        return video_id
