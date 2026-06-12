"""Download audio from any yt-dlp-supported platform.

A failed download raises a structured ``PipelineError`` (per S7) —
extractor breakage is an expected, user-explainable failure, not an
internal error. Authentication-required failures carry the distinct code
``download_auth_required`` with a neutral, hint-free message (per U2): the
raise site cannot know which face is running, so the hint is authored by
the face — the CLI prints the ``YT_DLP_COOKIES`` advice, the engine job
store maps the extension + cookies-file-import hint. Everything else is
``download_failed``.

When the resolved yt-dlp is the *managed* copy (it resides in the
user-data tools dir), a ``download_failed`` failure triggers one
``yt-dlp -U`` self-update and exactly one retry (per Q3): the gate is
residence alone — no engine/CLI flag — so any caller (engine job or CLI)
heals identically whenever the managed copy is in play, while PATH/pip
copies (dev environments) are never touched. ``download_auth_required``
bypasses the retry (per U2): an update cannot conjure missing credentials.
"""

from __future__ import annotations

import time
from pathlib import Path  # noqa: TC003 — used at runtime in glob/return
from typing import TYPE_CHECKING

from podcast_reader.engine.managed_tools import (
    is_managed,
    record_ytdlp_update,
    run_ytdlp_self_update,
)
from podcast_reader.engine.settings import data_dir_path
from podcast_reader.tools import resolve_tool, run_child
from podcast_reader.types import PipelineError, PipelineEvent

if TYPE_CHECKING:
    from collections.abc import Callable


def build_download_args(url: str, output_dir: Path, cookies: Path | None = None) -> list[str]:
    """Build the yt-dlp command-line arguments for audio extraction."""
    args = [
        resolve_tool("yt-dlp"),
        "-x",
        "--audio-format",
        "mp3",
    ]
    if cookies is not None:
        args.extend(["--cookies", str(cookies)])
    args.extend(["-o", str(output_dir / "%(id)s.%(ext)s"), url])
    return args


def build_title_args(url: str) -> list[str]:
    """Build the yt-dlp command-line arguments for title extraction."""
    return [resolve_tool("yt-dlp"), "--print", "title", url]


def fetch_title(url: str) -> str:
    """Fetch the video/post title using yt-dlp."""
    result = run_child(build_title_args(url))
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed to fetch title: {result.stderr.strip()}")
    return result.stdout.strip()


def download_audio(
    url: str,
    output_dir: Path,
    cookies: Path | None = None,
    on_event: Callable[[PipelineEvent], None] | None = None,
) -> Path:
    """Download and extract audio as mp3 from a URL.

    Returns the path to the downloaded mp3 file. A yt-dlp failure raises a
    structured ``download_failed`` error (per S7), or ``download_auth_required``
    for auth-detected failures (per U2). When the resolved yt-dlp is the
    managed user-data copy, a ``download_failed`` failure first triggers a
    single ``yt-dlp -U`` + retry (per Q3) with a warning event — extractor
    breakage heals in-job without an app release. A second failure surfaces
    the structured error; there are no further retries, and
    ``download_auth_required`` never retries at all.
    """
    try:
        return _download_once(url, output_dir, cookies)
    except PipelineError as exc:
        if exc.code != "download_failed":
            # download_auth_required (per U2): a yt-dlp update cannot conjure
            # missing credentials — surface immediately, no -U, no retry.
            raise
        binary = resolve_tool("yt-dlp")
        if not is_managed(binary):
            raise
        if on_event is not None:
            on_event(
                PipelineEvent(
                    kind="warning",
                    step="download",
                    message=(
                        "yt-dlp download failed; self-updating the managed yt-dlp and retrying once"
                    ),
                    data={"code": "ytdlp_self_update"},
                )
            )
        version = run_ytdlp_self_update(binary)
        if version is not None:
            # Keep the recorded version current so a later, older bundle seed
            # never clobbers the self-updated copy (newer-wins seeding).
            record_ytdlp_update(data_dir_path(), version, time.time())
        return _download_once(url, output_dir, cookies)


def _download_once(url: str, output_dir: Path, cookies: Path | None) -> Path:
    """One download attempt: run yt-dlp, locate the mp3, write the URL marker."""
    args = build_download_args(url, output_dir, cookies)
    result = run_child(args)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Auth-detected failures get the distinct code with a neutral message
        # and NO hint (per U2) — the face authors its own affordances.
        auth_required = "login" in stderr.lower() or "auth" in stderr.lower()
        code = "download_auth_required" if auth_required else "download_failed"
        raise PipelineError(code, f"yt-dlp failed: {stderr}", "")

    # Find the downloaded mp3 file
    mp3_files = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp3_files:
        raise RuntimeError(f"yt-dlp completed but no mp3 file found in {output_dir}")
    audio_path = mp3_files[0]

    # Write a marker so cache lookup can identify yt-dlp downloads unambiguously
    marker = audio_path.with_suffix(".ytdlp")
    marker.write_text(url)

    return audio_path
