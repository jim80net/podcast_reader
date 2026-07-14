"""Main CLI entry point for podcast-reader.

A thin adapter over :mod:`podcast_reader.pipeline`: one-shot mode prints
pipeline events to stdout; the ``serve`` subcommand starts the engine.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from podcast_reader.engine.settings import data_dir_path, load_settings
from podcast_reader.pipeline import PipelineError, _wsl_path, run_pipeline
from podcast_reader.providers import build_provider_registry
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
    elif argv and argv[0] == "serve-guardian":
        _run_serve_guardian(argv[1:])
    else:
        _run_one_shot(argv)


def serve_engine(*, discovery_file: Path | None = None) -> None:
    """Start the localhost engine (lazy import keeps one-shot startup light)."""
    from podcast_reader.engine.process import serve_engine as _serve_engine

    _serve_engine(discovery_file=discovery_file)


def run_serve_guardian(*, engine_port: int, tailscale_argv: list[str]) -> int:
    """Run the packaged private-web guardian (lazy import for one-shot mode)."""
    from podcast_reader.engine.serve_guardian import run_guardian

    return run_guardian(engine_port=engine_port, tailscale_argv=tailscale_argv)


def _run_serve_guardian(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="podcast-reader serve-guardian",
        description="Own a foreground Tailscale Serve lease for the desktop app",
    )
    parser.add_argument("--engine-port", type=int, required=True)
    parser.add_argument(
        "--tailscale-command-json",
        default='["tailscale"]',
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        command: object = json.loads(args.tailscale_command_json)
    except json.JSONDecodeError:
        parser.error("--tailscale-command-json must be a JSON argv array")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        parser.error("--tailscale-command-json must be a non-empty JSON string array")
    code = run_serve_guardian(engine_port=args.engine_port, tailscale_argv=command)
    if code != 0:
        raise SystemExit(code)


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
        "--provider",
        default="anthropic",
        help="Built-in or user-defined chapter LLM provider (default: anthropic)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Chapter model (default: the selected provider's default model)",
    )
    parser.add_argument(
        "--cleanup-captions",
        action="store_true",
        help="Use the chapter model for labeled spelling/casing cleanup (never rewording)",
    )
    args = parser.parse_args(argv)
    settings = load_settings(data_dir_path())
    registry = build_provider_registry(settings["custom_providers"])
    try:
        provider_spec = registry[args.provider]
    except KeyError:
        parser.error(f"unknown chapter provider: {args.provider!r}")

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
        chapter_provider=args.provider,
        chapter_api_key=os.environ.get(provider_spec["key_env"]),
        custom_provider_url=os.environ.get("PODCAST_READER_CUSTOM_PROVIDER_URL", ""),
        custom_providers=settings["custom_providers"],
        # CLI diarization stays the whisper-ctranslate2 --hf_token path; the
        # pack-based diarize step is an engine setting (desktop app).
        diarize=False,
        caption_cleanup=args.cleanup_captions,
    )

    try:
        result = run_pipeline(request, on_event=_print_event)
    except PipelineError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        hint = _cli_hint(exc)
        if hint:
            print(hint, file=sys.stderr)
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


def _cli_hint(exc: PipelineError) -> str:
    """The CLI-face hint for a pipeline failure.

    ``download_auth_required`` is raised with a neutral message and no hint
    (per U2) — the face authors its own affordances, and the CLI's is the
    ``YT_DLP_COOKIES`` environment variable (copy unchanged from before the
    split). Hints authored at the raise site pass through verbatim.
    """
    if exc.code == "download_auth_required" and not exc.hint:
        return "Set YT_DLP_COOKIES to a cookies file path for authenticated content."
    return exc.hint


def _print_event(event: PipelineEvent) -> None:
    """Print a pipeline event's message to stdout (the CLI's progress face)."""
    if event["message"] and event["kind"] != "job_done":
        print(event["message"])
