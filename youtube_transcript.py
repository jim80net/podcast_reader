#!/usr/bin/env python3
"""Fetch YouTube captions and output whisper-compatible JSON."""

import argparse
import json
import re
import sys
from pathlib import Path

import urllib.request

from youtube_transcript_api import YouTubeTranscriptApi


_YT_PATTERNS = [
    re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})'),
    re.compile(r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})'),
    re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})'),
]


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from a URL. Returns None if not a YouTube URL."""
    for pattern in _YT_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def snippets_to_whisper_segments(snippets: list[dict]) -> dict:
    """Convert youtube-transcript-api snippets to whisper-ctranslate2 JSON format."""
    segments = []
    for s in snippets:
        text = s["text"].strip()
        if not text:
            continue
        segments.append({
            "start": s["start"],
            "end": s["start"] + s["duration"],
            "text": text,
        })
    return {"segments": segments}


def fetch_transcript(video_id: str) -> list[dict]:
    """Fetch transcript for a YouTube video. Prefers manual captions over auto-generated."""
    from youtube_transcript_api import NoTranscriptFound

    ytt_api = YouTubeTranscriptApi()
    transcript_list = ytt_api.list(video_id)

    try:
        transcript = transcript_list.find_transcript(["en"])
    except NoTranscriptFound:
        raise SystemExit(f"Error: No English transcript available for {video_id}")

    fetched = transcript.fetch()
    return fetched.to_raw_data()


def fetch_video_title(video_id: str) -> str:
    """Fetch the video title from YouTube's oembed endpoint."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("title", video_id)
    except Exception:
        return video_id


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube captions as whisper-compatible JSON")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--output", default=None, help="Output JSON path (default: <video_id>.json)")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    if not video_id:
        print(f"Error: Not a recognized YouTube URL: {args.url}", file=sys.stderr)
        sys.exit(1)

    title = fetch_video_title(video_id)
    print(f"Video: {title}")
    print(f"Fetching transcript for {video_id}...")

    snippets = fetch_transcript(video_id)
    data = snippets_to_whisper_segments(snippets)

    out_path = Path(args.output) if args.output else Path(f"{video_id}.json")
    out_path.write_text(json.dumps(data, indent=2))
    print(f"Written {len(data['segments'])} segments to {out_path}")

    # Write the title to a sidecar file for transcribe.sh to pick up
    title_path = out_path.with_suffix(".title")
    title_path.write_text(title)


if __name__ == "__main__":
    main()
