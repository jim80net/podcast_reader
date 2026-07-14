"""Shared transcription pipeline with typed progress events.

Both the CLI one-shot mode and the engine job runner execute this single
orchestration; they differ only in the ``on_event`` consumer (stdout printing
vs job-store persistence).
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from podcast_reader.caption_cleanup import apply_caption_corrections
from podcast_reader.chapters import (
    ChapterError,
    format_transcript,
    generate_chapters,
    generate_chapters_with_cleanup,
    snap_chapters_to_segments,
)
from podcast_reader.diarize import diarize_step
from podcast_reader.html import build_html
from podcast_reader.providers import PROVIDERS, resolve_provider
from podcast_reader.tools import run_child
from podcast_reader.transcribe import transcribe, transcription_engine

# PipelineError is re-imported under its own name: an explicit re-export
# (mypy strict) for the existing `from podcast_reader.pipeline import
# PipelineError` consumers (CLI, engine job store).
from podcast_reader.types import PipelineError as PipelineError
from podcast_reader.types import PipelineEvent, PipelineResult
from podcast_reader.youtube import (
    NoTranscriptError,
    extract_video_id,
    fetch_transcript,
    fetch_video_title,
    snippets_to_whisper_segments,
)
from podcast_reader.ytdlp import download_audio, fetch_title

if TYPE_CHECKING:
    from collections.abc import Callable

    from podcast_reader.types import EventKind, PipelineRequest, StepName


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


def run_pipeline(
    request: PipelineRequest,
    on_event: Callable[[PipelineEvent], None],
) -> PipelineResult:
    """Run the full transcription pipeline, reporting progress via *on_event*."""
    source = request["source"]
    title = request["title"]
    output_dir = Path(request["output_dir"])
    cookies = Path(request["cookies"]) if request["cookies"] else None

    input_type = classify_input(source)
    stem: str
    json_path: Path
    transcript_source: str

    _emit(on_event, "step_started", "resolve", "", {})

    if input_type == InputType.YOUTUBE:
        video_id = extract_video_id(source)
        if not video_id:
            raise PipelineError(
                "invalid_input",
                f"Could not extract video ID from: {source}",
                "Check that the URL is a valid YouTube watch/share link.",
            )

        stem = video_id
        json_path = output_dir / f"{stem}.json"
        transcript_source = "youtube-captions"

        if title is None:
            title = fetch_video_title(video_id)
            _emit(on_event, "step_finished", "resolve", f"Video: {title}", {})
        else:
            _emit(on_event, "step_finished", "resolve", "", {})

        if _valid_artifact(json_path):
            _emit(
                on_event,
                "step_started",
                "captions",
                f"Transcript JSON already exists: {json_path} (delete to re-fetch)",
                {"cached": True},
            )
            _emit(on_event, "step_finished", "captions", "", {"cached": True})
        else:
            _emit(
                on_event,
                "step_started",
                "captions",
                f"Fetching transcript for {video_id}...",
                {},
            )
            try:
                snippets = fetch_transcript(video_id)
            except NoTranscriptError as exc:
                raise PipelineError(
                    "no_transcript",
                    str(exc),
                    "Only videos with English captions are supported on the captions path.",
                ) from exc
            data = snippets_to_whisper_segments(snippets)
            json_path.write_text(json.dumps(data, indent=2))
            _emit(
                on_event,
                "step_finished",
                "captions",
                f"Written {len(data['segments'])} segments to {json_path}",
                {},
            )

        if request["diarize"]:
            # Captions are fetched text — there is no audio to diarize. Say
            # so instead of silently ignoring the enabled setting.
            _emit(
                on_event,
                "warning",
                "diarize",
                "Diarization skipped: YouTube captions provide no audio to diarize",
                {"code": "diarization_skipped"},
            )

    elif input_type == InputType.URL:
        if title is None:
            try:
                title = fetch_title(source)
                _emit(on_event, "step_finished", "resolve", f"Video: {title}", {})
            except RuntimeError:
                title = None  # will derive from stem later
                _emit(on_event, "step_finished", "resolve", "", {})
        else:
            _emit(on_event, "step_finished", "resolve", "", {})

        # Check for a .ytdlp marker left by a previous download (match by URL)
        cached_marker = _find_ytdlp_marker(output_dir, source)
        audio_path: Path
        if cached_marker is not None:
            audio_path = cached_marker.with_suffix(".mp3")
            _emit(
                on_event,
                "step_started",
                "download",
                f"Audio already exists: {audio_path} (delete to re-download)",
                {"cached": True},
            )
            _emit(on_event, "step_finished", "download", "", {"cached": True})
        else:
            _emit(on_event, "step_started", "download", "Downloading with yt-dlp...", {})
            audio_path = download_audio(source, output_dir, cookies=cookies, on_event=on_event)
            _emit(on_event, "step_finished", "download", "", {})
        stem = audio_path.stem
        json_path = output_dir / f"{stem}.json"
        transcript_source = transcription_engine()

        _transcribe_if_needed(
            audio_path=audio_path,
            json_path=json_path,
            output_dir=output_dir,
            whisper_model=request["whisper_model"],
            whisper_lang=request["whisper_lang"],
            whisper_device=request["whisper_device"],
            hf_token=request["hf_token"],
            on_event=on_event,
        )
        if request["diarize"]:
            diarize_step(audio_path=audio_path, json_path=json_path, on_event=on_event)

    else:
        audio_path = Path(source).resolve()
        if not audio_path.exists():
            raise PipelineError(
                "not_found",
                f"File not found: {audio_path}",
                "Check the path and try again.",
            )

        _emit(on_event, "step_finished", "resolve", "", {})

        stem = audio_path.stem
        json_path = output_dir / f"{stem}.json"
        transcript_source = transcription_engine()

        _transcribe_if_needed(
            audio_path=audio_path,
            json_path=json_path,
            output_dir=output_dir,
            whisper_model=request["whisper_model"],
            whisper_lang=request["whisper_lang"],
            whisper_device=request["whisper_device"],
            hf_token=request["hf_token"],
            on_event=on_event,
        )
        if request["diarize"]:
            diarize_step(audio_path=audio_path, json_path=json_path, on_event=on_event)

    # --- Generate chapters (optional; never fatal — spec: chapters fault isolation) ---
    chapters_path = output_dir / f"{stem}_chapters.json"
    cleanup_path = output_dir / f"{stem}_caption_cleanup.json"
    chapters: list[dict[str, Any]] | None = None
    caption_corrections: list[object] = []
    cleanup_completed = False
    cleanup_requested = request["caption_cleanup"] and transcript_source == "youtube-captions"
    if request["caption_cleanup"] and not cleanup_requested:
        _emit(
            on_event,
            "warning",
            "chapters",
            "Caption cleanup applies only to YouTube caption sources; leaving wording unchanged",
            {"code": "caption_cleanup_skipped"},
        )

    provider = request["chapter_provider"]
    cleanup_cache_ready = not cleanup_requested or _valid_artifact(cleanup_path)
    if _valid_artifact(chapters_path) and cleanup_cache_ready:
        _emit(
            on_event,
            "step_started",
            "chapters",
            f"Chapters JSON already exists: {chapters_path} (delete to regenerate)",
            {"cached": True},
        )
        chapters = json.loads(chapters_path.read_text())
        if cleanup_requested:
            caption_corrections = json.loads(cleanup_path.read_text())
            cleanup_completed = True
        _emit(on_event, "step_finished", "chapters", "", {"cached": True})
    elif request["chapter_api_key"]:
        _emit(
            on_event,
            "step_started",
            "chapters",
            f"Generating chapter markers via {provider}...",
            {},
        )
        try:
            try:
                spec = resolve_provider(provider, custom_base_url=request["custom_provider_url"])
            except ValueError as exc:
                # providers.py messages are self-authored (no response-body
                # content) — promote them so the warning carries the diagnosis.
                raise ChapterError(str(exc)) from exc
            data = json.loads(json_path.read_text())
            segments = [s for s in data["segments"] if s.get("text", "").strip()]
            transcript_text = format_transcript(segments)
            if cleanup_requested:
                chapters, caption_corrections = generate_chapters_with_cleanup(
                    transcript_text,
                    spec=spec,
                    model=request["model"],
                    api_key=request["chapter_api_key"],
                )
                cleanup_path.write_text(json.dumps(caption_corrections, indent=2))
                cleanup_completed = True
            else:
                cleanup_path.unlink(missing_ok=True)
                chapters = generate_chapters(
                    transcript_text,
                    spec=spec,
                    model=request["model"],
                    api_key=request["chapter_api_key"],
                )
            chapters = snap_chapters_to_segments(chapters, segments)
            chapters_path.write_text(json.dumps(chapters, indent=2))
        except Exception as exc:  # provider/parse/network — never fatal
            chapters = None
            # ChapterError messages contain no response-body content by
            # construction, so they surface verbatim. Everything else gets the
            # generic wrap (key redaction): the exception text may carry
            # provider response fragments (auth-error bodies echo the key),
            # so only the exception class name reaches events and the journal.
            detail = f": {exc}" if isinstance(exc, ChapterError) else f" ({type(exc).__name__})"
            _emit(
                on_event,
                "warning",
                "chapters",
                f"Chapter generation failed via {provider}{detail}; "
                "rendering a chapterless transcript",
                {"code": "chapters_failed"},
            )
        else:
            _emit(
                on_event,
                "step_finished",
                "chapters",
                f"Written {len(chapters)} chapters to {chapters_path}",
                {},
            )
    else:
        _emit(
            on_event,
            "warning",
            "chapters",
            f"Skipping chapter generation ({_chapter_key_hint(provider)})",
            {"code": "chapters_skipped"},
        )

    # --- Convert to HTML ---
    if title is None:
        title = stem.replace("_", " ").replace("-", " ").title()

    html_path = output_dir / f"{stem}.html"
    data = json.loads(json_path.read_text())
    segments = [s for s in data["segments"] if s.get("text", "").strip()]
    cleanup_count = 0
    if cleanup_completed:
        segments, cleanup_count = apply_caption_corrections(segments, caption_corrections)

    _emit(on_event, "step_started", "render", "Generating HTML transcript...", {})
    html_content = build_html(
        segments,
        title,
        chapters=chapters,
        sentences_per_para=request["sentences"],
        source=transcript_source,
        caption_cleanup=cleanup_completed,
    )
    html_path.write_text(html_content)
    _emit(on_event, "step_finished", "render", "", {"caption_corrections": cleanup_count})

    _emit(on_event, "job_done", None, "Done", {})
    return PipelineResult(
        json_path=str(json_path),
        chapters_path=str(chapters_path) if chapters is not None else None,
        html_path=str(html_path),
        title=title,
    )


def _emit(
    on_event: Callable[[PipelineEvent], None],
    kind: EventKind,
    step: StepName | None,
    message: str,
    data: dict[str, Any],
) -> None:
    """Build and dispatch a PipelineEvent."""
    on_event(PipelineEvent(kind=kind, step=step, message=message, data=data))


def _chapter_key_hint(provider: str) -> str:
    """Provider-aware hint for the ``chapters_skipped`` warning."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        return f"unknown chapter provider {provider!r}"
    return f"set {spec['key_env']} or push a key via the app to enable"


def _valid_artifact(path: Path) -> bool:
    """Check a cached artifact: JSON must parse, HTML must be non-empty.

    Invalid artifacts are unlinked and treated as cache misses so the
    producing step re-runs (shared by CLI and engine cache checks).
    """
    if not path.exists():
        return False
    try:
        if path.suffix == ".json":
            json.loads(path.read_text())
        elif path.stat().st_size == 0:
            raise ValueError("empty artifact")
    except (ValueError, OSError):
        # Cleanup is best-effort: a permission error here must still report
        # a cache miss instead of crashing the pipeline.
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return False
    return True


def _wsl_path(path: Path) -> str | None:
    """Convert a Linux path to a Windows path if running in WSL."""
    if shutil.which("wslpath") is None:
        return None
    try:
        result = run_child(["wslpath", "-w", str(path)])
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _find_ytdlp_marker(output_dir: Path, url: str) -> Path | None:
    """Find a .ytdlp marker whose content matches *url* and whose mp3 still exists.

    Returns the marker path, or None if no valid cached download exists.
    Removes orphaned markers (mp3 deleted but marker remains). An unreadable
    marker is skipped (cache miss) rather than aborting processing.
    """
    for marker in output_dir.glob("*.ytdlp"):
        try:
            if marker.read_text().strip() == url and marker.with_suffix(".mp3").exists():
                return marker
            if not marker.with_suffix(".mp3").exists():
                marker.unlink()
        except OSError:
            continue
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
    on_event: Callable[[PipelineEvent], None],
) -> None:
    """Run whisper transcription if a valid JSON output doesn't already exist."""
    if _valid_artifact(json_path):
        _emit(
            on_event,
            "step_started",
            "transcribe",
            f"Transcript JSON already exists: {json_path} (delete to re-transcribe)",
            {"cached": True},
        )
        _emit(on_event, "step_finished", "transcribe", "", {"cached": True})
        return
    _emit(
        on_event,
        "step_started",
        "transcribe",
        f"Transcribing with {transcription_engine()} "
        f"(model={whisper_model}, lang={whisper_lang}, device={whisper_device})...",
        {},
    )
    transcribe(
        audio_path=audio_path,
        output_dir=output_dir,
        model=whisper_model,
        lang=whisper_lang,
        device=whisper_device,
        hf_token=hf_token,
        on_event=on_event,
    )
    _emit(on_event, "step_finished", "transcribe", "", {})
