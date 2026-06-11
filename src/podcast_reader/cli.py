"""Main CLI entry point for podcast-reader.

A thin adapter over :mod:`podcast_reader.pipeline`: one-shot mode prints
pipeline events to stdout; the ``serve`` subcommand starts the engine.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from podcast_reader.pipeline import PipelineError, _wsl_path, run_pipeline
from podcast_reader.types import PipelineEvent, PipelineRequest


def main() -> None:
    """CLI entry point."""
    main_with_args(sys.argv[1:])


def main_with_args(argv: list[str]) -> None:
    """Dispatch *argv*: ``serve`` starts the engine, anything else is one-shot.

    ``serve`` is detected as the first positional because argparse subparsers
    would break the legacy ``podcast-reader <url> [title]`` shape.
    """
    if argv and argv[0] == "serve":
        _run_serve(argv[1:])
    else:
        _run_one_shot(argv)


def serve_engine(*, discovery_file: Path | None = None) -> None:
    """Start the localhost engine (lazy import keeps one-shot startup light)."""
    from podcast_reader.engine.process import serve_engine as _serve_engine

    _serve_engine(discovery_file=discovery_file)


def _run_serve(argv: list[str]) -> None:
    """Parse ``serve`` arguments and start the engine."""
    parser = argparse.ArgumentParser(
        prog="podcast-reader serve",
        description="Run the localhost transcription engine",
    )
    parser.add_argument(
        "--discovery-file",
        type=Path,
        default=None,
        help="Path for the engine discovery file (default: <data_dir>/engine.json)",
    )
    args = parser.parse_args(argv)
    serve_engine(discovery_file=args.discovery_file)


def _run_one_shot(argv: list[str]) -> None:
    """Run the pipeline once, printing progress and result paths."""
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
    args = parser.parse_args(argv)

    request = PipelineRequest(
        source=args.input,
        title=args.title,
        output_dir=str(args.output_dir),
        model=args.model,
        whisper_model=os.environ.get("WHISPER_MODEL", "large-v3"),
        whisper_lang=os.environ.get("WHISPER_LANG", "en"),
        whisper_device=os.environ.get("WHISPER_DEVICE", "cuda"),
        hf_token=os.environ.get("HF_TOKEN"),
        sentences=int(os.environ.get("SENTENCES", "5")),
        cookies=os.environ.get("YT_DLP_COOKIES"),
    )

    try:
        result = run_pipeline(request, on_event=_print_event)
    except PipelineError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(exc.hint, file=sys.stderr)
        sys.exit(1)

    print()
    print("Done! Output files:")
    print(f"  JSON: {result['json_path']}")
    if result["chapters_path"] is not None:
        print(f"  Chapters: {result['chapters_path']}")
    print(f"  HTML: {result['html_path']}")

    win_path = _wsl_path(Path(result["html_path"]))
    if win_path:
        print(f"  Windows: {win_path}")


def _print_event(event: PipelineEvent) -> None:
    """Print a pipeline event's message to stdout (the CLI's progress face)."""
    if event["message"] and event["kind"] != "job_done":
        print(event["message"])
