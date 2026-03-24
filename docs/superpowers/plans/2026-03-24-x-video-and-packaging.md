# X Video Support & Python Package Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate podcast_reader from a shell script to a distributable Python CLI package with yt-dlp support for X/Twitter and other platforms.

**Architecture:** Six Python modules under `src/podcast_reader/` with a CLI entry point. Existing logic moves unchanged into modules; new `ytdlp.py` and `transcribe.py` wrap CLI tools via subprocess. All code strictly typed, formatted with ruff, tested with pytest.

**Tech Stack:** Python 3.10+, uv, hatchling, argparse, yt-dlp, whisper-ctranslate2, anthropic, youtube-transcript-api, mypy, ruff, pytest

**Spec:** `docs/superpowers/specs/2026-03-24-x-video-and-packaging-design.md`

---

### Task 1: Create pyproject.toml and package skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/podcast_reader/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "podcast-reader"
version = "0.1.0"
description = "Transcribe podcast audio or YouTube/X videos to styled HTML transcripts"
requires-python = ">=3.10"
dependencies = [
    "anthropic>=0.40",
    "youtube-transcript-api>=1.0",
    "yt-dlp>=2024.1.1",
]

[project.optional-dependencies]
whisper = [
    "whisper-ctranslate2>=0.5.7",
]
diarization = [
    "pyannote.audio>=4.0",
]
dev = [
    "pytest>=7.0",
    "mypy>=1.10",
    "ruff>=0.4",
]

[project.scripts]
podcast-reader = "podcast_reader.cli:main"

[tool.mypy]
strict = true

[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "SIM", "TCH"]

[tool.pytest.ini_options]
markers = [
    "integration: tests that download from the internet or require external tools",
]
```

- [ ] **Step 2: Create package init**

```python
# src/podcast_reader/__init__.py
"""Transcribe podcast audio or YouTube/X videos to styled HTML transcripts."""
```

- [ ] **Step 3: Run `uv sync --dev` to verify the package structure resolves**

Run: `timeout 120 uv sync --dev`
Expected: lockfile created, dependencies installed (cli entry point will fail since `cli.py` doesn't exist yet — that's fine, sync should still succeed)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/podcast_reader/__init__.py uv.lock
git commit -m "feat: create pyproject.toml and package skeleton"
```

---

### Task 2: Move youtube.py (from youtube_transcript.py)

**Files:**
- Create: `src/podcast_reader/youtube.py`
- Move: `tests/test_youtube_transcript.py` → `tests/test_youtube.py`
- Reference: `youtube_transcript.py` (source, do not delete yet)

- [ ] **Step 1: Update test file — copy and fix imports**

Copy `tests/test_youtube_transcript.py` to `tests/test_youtube.py`. Replace the `sys.path` hack and old import:

```python
"""Tests for podcast_reader.youtube module."""

from podcast_reader.youtube import extract_video_id, snippets_to_whisper_segments
```

Keep all existing test classes and methods unchanged.

- [ ] **Step 2: Run tests to verify they fail (module doesn't exist yet)**

Run: `timeout 30 uv run pytest tests/test_youtube.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podcast_reader.youtube'`

- [ ] **Step 3: Create `src/podcast_reader/youtube.py`**

Copy the contents of `youtube_transcript.py` into `src/podcast_reader/youtube.py` with these changes:
- Remove the `main()` function and `if __name__ == "__main__"` block
- Remove `import argparse`, `import sys` (no longer needed without main)
- Remove the `.title` sidecar file write (handled by cli.py now)
- Add full type annotations to all functions
- Keep all existing function logic unchanged

```python
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
        segments.append({
            "start": s["start"],
            "end": s["start"] + s["duration"],
            "text": text,
        })
    return {"segments": segments}


def fetch_transcript(video_id: str) -> list[dict[str, Any]]:
    """Fetch transcript for a YouTube video. Prefers manual captions over auto-generated."""
    from youtube_transcript_api import NoTranscriptFound

    ytt_api = YouTubeTranscriptApi()
    transcript_list = ytt_api.list(video_id)

    try:
        transcript = transcript_list.find_transcript(["en"])
    except NoTranscriptFound:
        raise SystemExit(f"Error: No English transcript available for {video_id}")

    fetched = transcript.fetch()
    return fetched.to_raw_data()  # type: ignore[no-any-return]


def fetch_video_title(video_id: str) -> str:
    """Fetch the video title from YouTube's oembed endpoint."""
    url = (
        f"https://www.youtube.com/oembed?"
        f"url=https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("title", video_id)  # type: ignore[no-any-return]
    except Exception:
        return video_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 30 uv run pytest tests/test_youtube.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `timeout 30 uv run ruff check src/podcast_reader/youtube.py && timeout 30 uv run ruff format --check src/podcast_reader/youtube.py && timeout 60 uv run mypy src/podcast_reader/youtube.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/podcast_reader/youtube.py tests/test_youtube.py
git commit -m "feat: move youtube_transcript.py to podcast_reader.youtube module"
```

---

### Task 3: Move chapters.py (from generate_chapters.py)

**Files:**
- Create: `src/podcast_reader/chapters.py`
- Move: `tests/test_generate_chapters.py` → `tests/test_chapters.py`
- Reference: `generate_chapters.py` (source, do not delete yet)

- [ ] **Step 1: Update test file — copy and fix imports**

Copy `tests/test_generate_chapters.py` to `tests/test_chapters.py`. Replace the `sys.path` hack and old import:

```python
"""Tests for chapter timestamp snapping in podcast_reader.chapters."""

from podcast_reader.chapters import snap_chapters_to_segments
```

Keep all existing test classes and methods unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 30 uv run pytest tests/test_chapters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podcast_reader.chapters'`

- [ ] **Step 3: Create `src/podcast_reader/chapters.py`**

Copy the contents of `generate_chapters.py` into `src/podcast_reader/chapters.py` with these changes:
- Remove the `main()` function and `if __name__ == "__main__"` block
- Remove `import argparse`, `import os`, `import sys` (no longer needed without main)
- Add full type annotations
- Keep all existing function logic and `SYSTEM_PROMPT` unchanged

```python
"""Generate chapter markers with abstracts from a whisper transcript using Claude."""

from __future__ import annotations

import json
from typing import Any

import anthropic


def _nearest_segment_time(target: float, seg_starts: list[float]) -> float:
    """Return the segment start time closest to *target*."""
    if not seg_starts:
        return target
    best = seg_starts[0]
    best_dist = abs(target - best)
    for t in seg_starts[1:]:
        d = abs(target - t)
        if d < best_dist:
            best, best_dist = t, d
    return best


def snap_chapters_to_segments(
    chapters: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Snap all chapter timestamps to the nearest real segment timestamp.

    LLMs sometimes hallucinate timestamps that don't exist in the transcript.
    This post-processing step ensures every chapter boundary aligns with an
    actual segment, preventing empty chapters and misplaced content.
    """
    if not chapters or not segments:
        return chapters

    seg_starts = sorted({s["start"] for s in segments})

    snapped: list[dict[str, Any]] = []
    for ch in chapters:
        ch = dict(ch)  # shallow copy
        ch["start"] = _nearest_segment_time(ch["start"], seg_starts)
        ch["end"] = _nearest_segment_time(ch["end"], seg_starts)
        if ch.get("paragraph_breaks"):
            ch["paragraph_breaks"] = [
                _nearest_segment_time(t, seg_starts) for t in ch["paragraph_breaks"]
            ]
        if ch.get("pull_quote_start") is not None:
            ch["pull_quote_start"] = _nearest_segment_time(ch["pull_quote_start"], seg_starts)
        snapped.append(ch)
    return snapped


SYSTEM_PROMPT = """\
You are a podcast analyst. Given a timestamped transcript, identify the natural \
chapter boundaries and produce a JSON array of chapters.

Each transcript line is formatted as [<seconds>] text. Use these seconds values \
directly in your output — copy them exactly from the transcript.

For each chapter, provide:
- "title": A concise, descriptive chapter title
- "start": Start time in seconds (copy from the first segment in the chapter)
- "end": End time in seconds (copy from the last segment in the chapter)
- "abstract": A 2-3 sentence summary of what is discussed in this chapter
- "type": One of "intro", "housekeeping", "content", "sponsor", "outro"
- "paragraph_breaks": An array of seconds-timestamps where a new paragraph should \
begin within this chapter. Each value is the seconds value from the transcript line \
of the first segment in that paragraph. The first value must equal the chapter's "start" time.
- "key_points": An array of strings — concise bullet points capturing the main arguments, \
claims, or facts in the chapter. May be an empty array for thin chapters (e.g. short intros \
or outros). Aim for 2-5 points per substantive chapter.
- "pull_quote": A standout phrase from the chapter suitable for a magazine-style callout, \
or null if nothing in the chapter merits highlighting. May be verbatim from the transcript \
or lightly edited to clean up filler words and spoken grammar while preserving the speaker's intent.
- "pull_quote_start": The seconds value from the transcript line where the pull \
quote begins. Required when "pull_quote" is non-null, omit or set to null otherwise.

Guidelines:
- Identify sponsor reads, ad segments, or promotional plugs as type "sponsor"
- Introductory greetings, theme music descriptions, or "welcome to the show" segments are "intro"
- Housekeeping like announcements, schedule updates, or meta-discussion about the podcast is "housekeeping"
- Closing remarks, sign-offs, or "thanks for listening" are "outro"
- Everything else is "content"
- Aim for chapters that represent meaningful topic shifts, not every minor tangent
- A typical 60-minute podcast has 5-15 chapters
- Chapters must be contiguous — every second of the podcast belongs to exactly one chapter

Key points guidelines:
- Key points should be substantive claims or arguments, not summaries \
(e.g. "80% of casualties in Ukraine are now drone-inflicted" not "Discusses drone casualties")
- Include specific numbers, names, or facts when the speaker provides them
- If a chapter lists items (e.g. "myth number one... myth number two..."), \
capture each item as a separate key point

Pull quote guidelines:
- Pick the single most striking, quotable statement — something that makes a reader \
want to read the section
- Not every chapter needs a pull quote — only include one if something genuinely stands out
- Prefer vivid, self-contained statements over ones that need surrounding context

Paragraph break guidelines:
- Break paragraphs at thematic boundaries — when the speaker shifts to a new point, \
example, argument, or sub-topic
- One coherent thought or argument per paragraph
- Do NOT break mechanically by sentence count — some paragraphs may be 2 sentences, \
others may be 8, depending on the content
- Use the seconds values from the transcript lines to identify where breaks should occur
- Each break must use an exact seconds value that appears in the transcript

Return ONLY the JSON array, no other text."""


def format_transcript(segments: list[dict[str, Any]]) -> str:
    """Format segments with timestamps in seconds for the LLM prompt."""
    lines: list[str] = []
    for seg in segments:
        start = seg["start"]
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(f"[{start:.1f}] {text}")
    return "\n".join(lines)


def generate_chapters(
    transcript_text: str, model: str = "claude-haiku-4-5-20251001"
) -> list[dict[str, Any]]:
    """Send transcript to Claude and get back structured chapters."""
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Here is the transcript:\n\n{transcript_text}",
            }
        ],
    )

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            "Claude response was truncated (hit max_tokens). "
            "The transcript may be too long for a single request."
        )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)  # type: ignore[no-any-return]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 30 uv run pytest tests/test_chapters.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `timeout 30 uv run ruff check src/podcast_reader/chapters.py && timeout 30 uv run ruff format --check src/podcast_reader/chapters.py && timeout 60 uv run mypy src/podcast_reader/chapters.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/podcast_reader/chapters.py tests/test_chapters.py
git commit -m "feat: move generate_chapters.py to podcast_reader.chapters module"
```

---

### Task 4: Move html.py (from json_to_html.py)

**Files:**
- Create: `src/podcast_reader/html.py`
- Move: `tests/test_json_to_html.py` → `tests/test_html.py`
- Reference: `json_to_html.py` (source, do not delete yet)

- [ ] **Step 1: Update test file — copy and fix imports**

Copy `tests/test_json_to_html.py` to `tests/test_html.py`. Replace the `sys.path` hack and old import:

```python
"""Tests for podcast_reader.html paragraph grouping."""

from podcast_reader.html import segments_to_paragraphs, _count_sentences
```

Keep all existing test classes and methods unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 30 uv run pytest tests/test_html.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podcast_reader.html'`

- [ ] **Step 3: Create `src/podcast_reader/html.py`**

Copy the entire contents of `json_to_html.py` into `src/podcast_reader/html.py` with these changes:
- Remove the `main()` function and `if __name__ == "__main__"` block
- Remove `import argparse` (no longer needed without main)
- Remove `import json` and `from pathlib import Path` (only used in removed `main()`)
- Add `from __future__ import annotations` at top
- Add full type annotations to all functions (use `dict[str, Any]` for segment/chapter dicts)
- Keep all existing function logic, CSS, and JS unchanged

The file is large (~580 lines with CSS/JS). Copy it verbatim from `json_to_html.py`, applying only the changes listed above. Do NOT alter the CSS (`_STYLESHEET`), JS (`_SCROLL_SCRIPT`), or any rendering logic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 30 uv run pytest tests/test_html.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `timeout 30 uv run ruff check src/podcast_reader/html.py && timeout 30 uv run ruff format --check src/podcast_reader/html.py && timeout 60 uv run mypy src/podcast_reader/html.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/podcast_reader/html.py tests/test_html.py
git commit -m "feat: move json_to_html.py to podcast_reader.html module"
```

---

### Task 5: Create ytdlp.py (new module)

**Files:**
- Create: `src/podcast_reader/ytdlp.py`
- Create: `tests/test_ytdlp.py`

- [ ] **Step 1: Write unit tests**

```python
"""Tests for podcast_reader.ytdlp module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_reader.ytdlp import build_download_args, build_title_args, download_audio, fetch_title


class TestBuildDownloadArgs:
    def test_basic_url(self) -> None:
        result = build_download_args("https://x.com/user/status/123", Path("/tmp/out"))
        assert result == [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "-o", "/tmp/out/%(id)s.%(ext)s",
            "https://x.com/user/status/123",
        ]

    def test_with_cookies(self) -> None:
        result = build_download_args(
            "https://x.com/user/status/123",
            Path("/tmp/out"),
            cookies=Path("/home/user/cookies.txt"),
        )
        assert result == [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--cookies", "/home/user/cookies.txt",
            "-o", "/tmp/out/%(id)s.%(ext)s",
            "https://x.com/user/status/123",
        ]


class TestBuildTitleArgs:
    def test_basic(self) -> None:
        result = build_title_args("https://x.com/user/status/123")
        assert result == [
            "yt-dlp",
            "--print", "title",
            "https://x.com/user/status/123",
        ]


class TestFetchTitle:
    def test_returns_stripped_title(self) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="My Video Title\n", stderr=""
            )
            result = fetch_title("https://x.com/user/status/123")
        assert result == "My Video Title"

    def test_raises_on_failure(self) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: not found"
            )
            with pytest.raises(RuntimeError, match="yt-dlp failed"):
                fetch_title("https://x.com/user/status/123")


class TestDownloadAudio:
    def test_returns_audio_path(self, tmp_path: Path) -> None:
        # Simulate yt-dlp creating the file
        expected_file = tmp_path / "123.mp3"
        expected_file.touch()

        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = download_audio("https://x.com/user/status/123", tmp_path)

        assert result == expected_file

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: login required"
            )
            with pytest.raises(RuntimeError, match="yt-dlp failed"):
                download_audio("https://x.com/user/status/123", tmp_path)

    def test_auth_error_suggests_cookies(self, tmp_path: Path) -> None:
        with patch("podcast_reader.ytdlp.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="ERROR: login required"
            )
            with pytest.raises(RuntimeError, match="cookies"):
                download_audio("https://x.com/user/status/123", tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 30 uv run pytest tests/test_ytdlp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podcast_reader.ytdlp'`

- [ ] **Step 3: Create `src/podcast_reader/ytdlp.py`**

```python
"""Download audio from any yt-dlp-supported platform."""

from __future__ import annotations

import subprocess
from pathlib import Path


def build_download_args(
    url: str, output_dir: Path, cookies: Path | None = None
) -> list[str]:
    """Build the yt-dlp command-line arguments for audio extraction."""
    args = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 30 uv run pytest tests/test_ytdlp.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `timeout 30 uv run ruff check src/podcast_reader/ytdlp.py && timeout 30 uv run ruff format --check src/podcast_reader/ytdlp.py && timeout 60 uv run mypy src/podcast_reader/ytdlp.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/podcast_reader/ytdlp.py tests/test_ytdlp.py
git commit -m "feat: add ytdlp module for yt-dlp audio extraction"
```

---

### Task 6: Create transcribe.py (new module)

**Files:**
- Create: `src/podcast_reader/transcribe.py`
- Create: `tests/test_transcribe.py`

- [ ] **Step 1: Write unit tests**

```python
"""Tests for podcast_reader.transcribe module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_reader.transcribe import build_whisper_args, transcribe


class TestBuildWhisperArgs:
    def test_basic_args(self) -> None:
        result = build_whisper_args(
            audio_path=Path("/tmp/episode.mp3"),
            output_dir=Path("/tmp/out"),
            model="large-v3",
            lang="en",
            device="cuda",
        )
        assert result == [
            "whisper-ctranslate2",
            "/tmp/episode.mp3",
            "--model", "large-v3",
            "--language", "en",
            "--device", "cuda",
            "--output_format", "json",
            "--output_dir", "/tmp/out",
            "--print_colors", "False",
        ]

    def test_with_hf_token(self) -> None:
        result = build_whisper_args(
            audio_path=Path("/tmp/episode.mp3"),
            output_dir=Path("/tmp/out"),
            model="large-v3",
            lang="en",
            device="cuda",
            hf_token="hf_abc123",
        )
        assert "--hf_token" in result
        idx = result.index("--hf_token")
        assert result[idx + 1] == "hf_abc123"

    def test_without_hf_token(self) -> None:
        result = build_whisper_args(
            audio_path=Path("/tmp/episode.mp3"),
            output_dir=Path("/tmp/out"),
            model="large-v3",
            lang="en",
            device="cpu",
        )
        assert "--hf_token" not in result


class TestTranscribe:
    def test_returns_json_path(self, tmp_path: Path) -> None:
        audio_file = tmp_path / "episode.mp3"
        audio_file.touch()
        expected_json = tmp_path / "episode.json"
        expected_json.write_text('{"segments": []}')

        with patch("podcast_reader.transcribe.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = transcribe(
                audio_path=audio_file,
                output_dir=tmp_path,
                model="large-v3",
                lang="en",
                device="cpu",
            )

        assert result == expected_json

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        audio_file = tmp_path / "episode.mp3"
        audio_file.touch()

        with patch("podcast_reader.transcribe.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="CUDA error"
            )
            with pytest.raises(RuntimeError, match="whisper-ctranslate2 failed"):
                transcribe(
                    audio_path=audio_file,
                    output_dir=tmp_path,
                    model="large-v3",
                    lang="en",
                    device="cuda",
                )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 30 uv run pytest tests/test_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podcast_reader.transcribe'`

- [ ] **Step 3: Create `src/podcast_reader/transcribe.py`**

```python
"""Orchestrate whisper-ctranslate2 transcription."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
        "--model", model,
        "--language", lang,
        "--device", device,
        "--output_format", "json",
        "--output_dir", str(output_dir),
        "--print_colors", "False",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 30 uv run pytest tests/test_transcribe.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `timeout 30 uv run ruff check src/podcast_reader/transcribe.py && timeout 30 uv run ruff format --check src/podcast_reader/transcribe.py && timeout 60 uv run mypy src/podcast_reader/transcribe.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/podcast_reader/transcribe.py tests/test_transcribe.py
git commit -m "feat: add transcribe module for whisper-ctranslate2 orchestration"
```

---

### Task 7: Create cli.py (main entry point)

**Files:**
- Create: `src/podcast_reader/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write unit tests for URL routing and argument parsing**

```python
"""Tests for podcast_reader.cli module."""

from __future__ import annotations

from podcast_reader.cli import classify_input, InputType


class TestClassifyInput:
    def test_youtube_standard(self) -> None:
        assert classify_input("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == InputType.YOUTUBE

    def test_youtube_short(self) -> None:
        assert classify_input("https://youtu.be/dQw4w9WgXcQ") == InputType.YOUTUBE

    def test_youtube_embed(self) -> None:
        assert classify_input("https://www.youtube.com/embed/dQw4w9WgXcQ") == InputType.YOUTUBE

    def test_x_url(self) -> None:
        assert classify_input("https://x.com/user/status/123456") == InputType.URL

    def test_twitter_url(self) -> None:
        assert classify_input("https://twitter.com/user/status/123456") == InputType.URL

    def test_vimeo_url(self) -> None:
        assert classify_input("https://vimeo.com/123456") == InputType.URL

    def test_direct_audio_url(self) -> None:
        assert classify_input("https://example.com/episode.mp3") == InputType.URL

    def test_http_url(self) -> None:
        assert classify_input("http://example.com/video") == InputType.URL

    def test_local_file(self) -> None:
        assert classify_input("/home/user/episode.mp3") == InputType.LOCAL_FILE

    def test_relative_file(self) -> None:
        assert classify_input("episode.mp3") == InputType.LOCAL_FILE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `timeout 30 uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'podcast_reader.cli'`

- [ ] **Step 3: Create `src/podcast_reader/cli.py`**

```python
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
            print(f"Error: Could not extract video ID from: {input_arg}", file=sys.stderr)
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

        # Check if audio was already downloaded (caching)
        existing_mp3s = list(output_dir.glob("*.mp3"))
        audio_path: Path | None = existing_mp3s[0] if existing_mp3s else None
        if audio_path is not None:
            print(f"Audio already exists: {audio_path} (delete to re-download)")
        else:
            print("Downloading with yt-dlp...")
            audio_path = download_audio(input_arg, output_dir, cookies=cookies)
        stem = audio_path.stem
        json_path = output_dir / f"{stem}.json"
        transcript_source = "whisper-ctranslate2"

        if json_path.exists():
            print(f"Transcript JSON already exists: {json_path} (delete to re-transcribe)")
        else:
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

    else:
        audio_path = Path(input_arg).resolve()
        if not audio_path.exists():
            print(f"Error: File not found: {audio_path}", file=sys.stderr)
            sys.exit(1)

        stem = audio_path.stem
        json_path = output_dir / f"{stem}.json"
        transcript_source = "whisper-ctranslate2"

        if json_path.exists():
            print(f"Transcript JSON already exists: {json_path} (delete to re-transcribe)")
        else:
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
        segments, title, chapters=chapters, sentences_per_para=sentences, source=transcript_source
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
        description="Transcribe podcast audio or YouTube/X videos to styled HTML transcripts",
    )
    parser.add_argument("input", help="URL or local file path")
    parser.add_argument("title", nargs="?", default=None, help="Document title (optional)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model for chapters (default: claude-haiku-4-5-20251001)",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `timeout 30 uv run pytest tests/test_cli.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `timeout 30 uv run ruff check src/podcast_reader/cli.py && timeout 30 uv run ruff format --check src/podcast_reader/cli.py && timeout 60 uv run mypy src/podcast_reader/cli.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/podcast_reader/cli.py tests/test_cli.py
git commit -m "feat: add CLI entry point replacing transcribe.sh"
```

---

### Task 8: Create test fixtures and integration tests

**Files:**
- Create: `tests/fixtures/sample_whisper.json`
- Create: `tests/fixtures/sample_chapters.json`
- Create: `tests/fixtures/sample_expected.html`
- Modify: `tests/test_html.py` (add integration test)

- [ ] **Step 1: Create fixtures directory and sample whisper JSON fixture**

```bash
mkdir -p tests/fixtures
```

Create `tests/fixtures/sample_whisper.json` — a small, realistic whisper JSON with 6 segments:

```json
{
  "segments": [
    {"start": 0.0, "end": 5.0, "text": "Welcome to the show. I'm your host."},
    {"start": 5.0, "end": 10.0, "text": "Today we're talking about Python packaging."},
    {"start": 10.0, "end": 15.0, "text": "First, let's discuss pyproject.toml files."},
    {"start": 15.0, "end": 20.0, "text": "They replace setup.py and setup.cfg."},
    {"start": 20.0, "end": 25.0, "text": "The build backend is usually hatchling."},
    {"start": 25.0, "end": 30.0, "text": "Thanks for listening to the show today."}
  ]
}
```

- [ ] **Step 2: Create sample chapters JSON fixture**

Create `tests/fixtures/sample_chapters.json`:

```json
[
  {
    "title": "Introduction",
    "start": 0.0,
    "end": 10.0,
    "abstract": "The host introduces the episode.",
    "type": "intro",
    "paragraph_breaks": [0.0],
    "key_points": [],
    "pull_quote": null,
    "pull_quote_start": null
  },
  {
    "title": "Python Packaging",
    "start": 10.0,
    "end": 25.0,
    "abstract": "Discussion of modern Python packaging with pyproject.toml.",
    "type": "content",
    "paragraph_breaks": [10.0, 20.0],
    "key_points": ["pyproject.toml replaces setup.py", "hatchling is the recommended build backend"],
    "pull_quote": "They replace setup.py and setup.cfg.",
    "pull_quote_start": 15.0
  },
  {
    "title": "Closing",
    "start": 25.0,
    "end": 30.0,
    "abstract": "The host signs off.",
    "type": "outro",
    "paragraph_breaks": [25.0],
    "key_points": [],
    "pull_quote": null,
    "pull_quote_start": null
  }
]
```

- [ ] **Step 3: Generate expected HTML fixture**

Write a test that generates HTML from the fixtures and saves it as the expected output. Run it once to create `tests/fixtures/sample_expected.html`:

Add to `tests/test_html.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class TestBuildHtmlIntegration:
    def test_full_pipeline_with_chapters(self) -> None:
        """Integration test: whisper JSON + chapters JSON → HTML matches expected output."""
        from podcast_reader.html import build_html

        whisper_data = json.loads((FIXTURES / "sample_whisper.json").read_text())
        chapters = json.loads((FIXTURES / "sample_chapters.json").read_text())
        segments = [s for s in whisper_data["segments"] if s.get("text", "").strip()]

        result = build_html(
            segments,
            title="Test Episode",
            chapters=chapters,
            sentences_per_para=5,
            source="test",
        )

        expected_path = FIXTURES / "sample_expected.html"
        if not expected_path.exists():
            # First run: generate the expected output
            expected_path.write_text(result)
            raise AssertionError(
                f"Expected HTML fixture did not exist. Generated it at {expected_path}. "
                "Review and re-run."
            )

        expected = expected_path.read_text()
        assert result == expected
```

- [ ] **Step 4: Run the test once to generate the expected HTML fixture**

Run: `timeout 30 uv run pytest tests/test_html.py::TestBuildHtmlIntegration -v`
Expected: FAIL with "Expected HTML fixture did not exist. Generated it at..."

- [ ] **Step 5: Review the generated HTML, then re-run to verify it passes**

Review `tests/fixtures/sample_expected.html` to verify it looks correct.

Run: `timeout 30 uv run pytest tests/test_html.py::TestBuildHtmlIntegration -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/ tests/test_html.py
git commit -m "test: add fixture-based integration tests for HTML pipeline"
```

---

### Task 9: Delete old files and run full test suite

**Files:**
- Delete: `transcribe.sh`
- Delete: `youtube_transcript.py`
- Delete: `generate_chapters.py`
- Delete: `json_to_html.py`
- Delete: `requirements.txt`
- Delete: `tests/test_youtube_transcript.py`
- Delete: `tests/test_json_to_html.py`
- Delete: `tests/test_generate_chapters.py`

- [ ] **Step 1: Delete old source files** (keep `tests/__init__.py` — it is intentionally retained)

```bash
git rm transcribe.sh youtube_transcript.py generate_chapters.py json_to_html.py requirements.txt
```

- [ ] **Step 2: Delete old test files**

```bash
git rm tests/test_youtube_transcript.py tests/test_json_to_html.py tests/test_generate_chapters.py
```

- [ ] **Step 3: Run full test suite**

Run: `timeout 60 uv run pytest -v -m "not integration"`
Expected: All tests PASS

- [ ] **Step 4: Run ruff on entire package**

Run: `timeout 30 uv run ruff check src/ tests/ && timeout 30 uv run ruff format --check src/ tests/`
Expected: No errors

- [ ] **Step 5: Run mypy on entire package**

Run: `timeout 60 uv run mypy src/`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove old shell script and standalone Python files

All functionality has been migrated to the podcast_reader package.
Old files replaced by:
- transcribe.sh → podcast_reader.cli
- youtube_transcript.py → podcast_reader.youtube
- generate_chapters.py → podcast_reader.chapters
- json_to_html.py → podcast_reader.html"
```

---

### Task 10: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md to reflect new package structure**

Update the following sections:
- Quick Start: replace `./transcribe.sh` with `uv run podcast-reader` or `podcast-reader`
- Setup: update install instructions to `uv sync --dev`
- Files table: replace old files with new module paths
- Pipeline: update to reference new modules
- Development: add code quality tools (ruff, mypy, pytest markers)

- [ ] **Step 2: Verify the doc is accurate by checking the commands work**

Run: `timeout 30 uv run podcast-reader --help`
Expected: help text prints with usage info

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for Python package structure"
```
