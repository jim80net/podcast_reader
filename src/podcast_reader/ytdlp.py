"""Download audio from any yt-dlp-supported platform.

A failed download raises a structured ``PipelineError`` (per S7) —
extractor breakage is an expected, user-explainable failure, not an
internal error. Authentication-required failures carry the distinct code
``download_auth_required`` with a short, reader-modeled hint; the full
stderr stays in the error detail. Everything else is ``download_failed``.

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

    #: One download attempt: locate-and-return the produced media path, or raise
    #: a structured :class:`PipelineError`. The audio and video paths each bind
    #: their own (url, output_dir, cookies); :func:`_download_with_self_heal`
    #: drives the managed-copy ``-U`` retry around whichever one it is given.
    DownloadAttempt = Callable[[], Path]

#: yt-dlp's actual auth-failure phrasings (per V6), matched case-insensitively.
#: Anchored phrases, not bare substrings: "login"/"auth" alone misrouted
#: extractor noise like "author info" or "OAuth" to download_auth_required,
#: and bare "--cookies"/"authentication" matched option mentions
#: (deprecation notices) and proxy-layer errors ("Proxy Authentication
#: Required") — all of which also suppressed the self-heal retry below.
#: "use --cookies" anchors yt-dlp's raise_login_required suggestion
#: ("Use --cookies-from-browser or --cookies ..."), which every
#: cookie-fixable failure carries.
_AUTH_STDERR_MARKERS = (
    "login required",
    "sign in to confirm",
    "use --cookies",
    "authentication needed",
)

#: Face-neutral hints for common non-auth download failures. Keep these tied
#: to stable yt-dlp phrases: the pipeline cannot assume whether the caller is
#: the terminal CLI or the desktop app.
_DOWNLOAD_FAILURE_HINTS = (
    (
        (
            "geo-restricted",
            "geo restriction",
            "not available in your country",
            "not available in your region",
            "blocked in your country",
        ),
        "This media is not available in your region.",
    ),
    (
        (
            "private video",
            "video is private",
            "private content",
            "has been removed",
            "removed by the uploader",
            "video unavailable",
        ),
        "This media is private or has been removed. Check that it is still available to you.",
    ),
    (
        (
            "http error 404",
            "404: not found",
            "404 not found",
            "requested url returned error: 404",
        ),
        "The media was not found. Check that the URL is correct and still available.",
    ),
)


def _terminal_error_line(stderr: str) -> str:
    """Return the last yt-dlp ``ERROR:`` line, or a reasonable fallback."""
    for line in reversed(stderr.strip().splitlines()):
        line = line.strip()
        if line.startswith("ERROR:"):
            message = line.removeprefix("ERROR:").strip()
            return message if message else line
    for line in reversed(stderr.strip().splitlines()):
        line = line.strip()
        if line:
            return line
    return "yt-dlp failed"


def _download_hint(code: str, stderr: str) -> str:
    """Return a face-neutral hint for a recognized non-auth failure."""
    if code != "download_failed":
        return ""
    stderr_lower = stderr.lower()
    for markers, hint in _DOWNLOAD_FAILURE_HINTS:
        if any(marker in stderr_lower for marker in markers):
            return hint
    return ""


def build_download_args(url: str, output_dir: Path, cookies: Path | None = None) -> list[str]:
    """Build the yt-dlp command-line arguments for audio extraction."""
    return _build_args(url, output_dir, cookies, ["-x", "--audio-format", "mp3"])


def build_video_args(url: str, output_dir: Path, cookies: Path | None = None) -> list[str]:
    """Build the yt-dlp command-line arguments for video acquisition.

    Format selector ``bv*+ba/b`` is best-video + best-audio, **falling back to
    the best single stream** (``/b``) when there is no separate video track —
    so audio-only remote posts still resolve (F7). ``--merge-output-format mp4``
    muxes the separate streams with the bundled ffmpeg (the same tool diarize.py
    resolves); a single-stream fallback is left in its native container.
    """
    return _build_args(url, output_dir, cookies, ["-f", "bv*+ba/b", "--merge-output-format", "mp4"])


def _build_args(
    url: str, output_dir: Path, cookies: Path | None, mode_args: list[str]
) -> list[str]:
    """Assemble a yt-dlp command: tool, mode-specific flags, cookies, output, url."""
    args = [resolve_tool("yt-dlp"), *mode_args]
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
    return _download_with_self_heal(
        lambda: _download_once_audio(url, output_dir, cookies), on_event
    )


def download_video(
    url: str,
    output_dir: Path,
    cookies: Path | None = None,
    on_event: Callable[[PipelineEvent], None] | None = None,
) -> Path:
    """Download video (or the audio-only fallback) as a single media file.

    The video twin of :func:`download_audio` (floating-video-player): same
    structured ``download_failed`` / ``download_auth_required`` codes and the
    same managed-copy ``-U``-and-retry self-heal, via the shared
    :func:`_download_with_self_heal` wrapper. Returns the path to whichever
    media file yt-dlp produced — an ``.mp4`` for a merged video, or the
    native single-stream container for an audio-only fallback (F7).
    """
    return _download_with_self_heal(
        lambda: _download_once_video(url, output_dir, cookies), on_event
    )


def _download_with_self_heal(
    attempt: DownloadAttempt,
    on_event: Callable[[PipelineEvent], None] | None,
) -> Path:
    """Run one download *attempt*, with the managed-copy ``-U``-and-retry heal.

    Shared by audio and video (per Q3/S7): a first ``download_failed`` against
    the managed user-data yt-dlp triggers a single ``yt-dlp -U`` + one retry
    with a warning event; ``download_auth_required`` (and any non-managed copy)
    surfaces immediately with no update and no retry.
    """
    try:
        return attempt()
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
        return attempt()


def _download_once_audio(url: str, output_dir: Path, cookies: Path | None) -> Path:
    """One audio attempt: run yt-dlp, locate the mp3, write the URL marker."""
    _run_download(build_download_args(url, output_dir, cookies))
    mp3_files = sorted(output_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp3_files:
        raise RuntimeError(f"yt-dlp completed but no mp3 file found in {output_dir}")
    audio_path = mp3_files[0]

    # Write a marker so cache lookup can identify yt-dlp downloads unambiguously
    marker = audio_path.with_suffix(".ytdlp")
    marker.write_text(url)
    return audio_path


#: Media container suffixes yt-dlp can emit (merged mp4, or the audio-only
#: fallback's single stream — F7). The picker matches only these so a sidecar
#: (.info.json, .jpg/.webp thumbnail, .part) written with a newer mtime can
#: never be mistaken for the media file (a fixed glob can't be used because the
#: output extension varies, unlike the audio path's *.mp3).
_MEDIA_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mkv",
        ".webm",
        ".mov",
        ".m4v",
        ".avi",
        ".ts",
        ".flv",
        ".3gp",  # video
        ".m4a",
        ".mp3",
        ".opus",
        ".ogg",
        ".oga",
        ".wav",
        ".flac",
        ".aac",  # audio
    }
)


def _download_once_video(url: str, output_dir: Path, cookies: Path | None) -> Path:
    """One video attempt: run yt-dlp, locate the newest produced media file.

    The format selector may yield an ``.mp4`` (merged) or, for audio-only
    sources (F7), a single-stream container, so the newest file whose suffix is
    a known media container is returned — sidecar files (metadata/thumbnail/
    partials) are ignored even if newer.
    """
    _run_download(build_video_args(url, output_dir, cookies))
    produced = sorted(
        (p for p in output_dir.iterdir() if p.is_file() and p.suffix.lower() in _MEDIA_SUFFIXES),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not produced:
        raise RuntimeError(f"yt-dlp completed but no media file found in {output_dir}")
    return produced[0]


def _run_download(args: list[str]) -> None:
    """Run a yt-dlp download, raising the structured error on a non-zero exit.

    Auth-detected failures get the distinct ``download_auth_required`` code
    with a neutral hint and the terminal stderr line as the user-facing
    message (per U2/V6). The raise site cannot know which face is running, so
    the CLI and engine author their own auth affordances. Recognized
    ``download_failed`` classes receive only face-neutral hints.
    """
    result = run_child(args)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        auth_required = any(marker in stderr.lower() for marker in _AUTH_STDERR_MARKERS)
        code = "download_auth_required" if auth_required else "download_failed"
        raise PipelineError(
            code,
            _terminal_error_line(stderr),
            _download_hint(code, stderr),
            stderr,
        )
