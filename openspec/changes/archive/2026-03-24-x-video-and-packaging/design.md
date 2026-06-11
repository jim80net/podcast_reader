## Context

The project had grown from a single shell script + a few Python utilities into something that needed real packaging and broader platform support (especially X/Twitter videos via yt-dlp). The design doc (`docs/superpowers/specs/2026-03-24-x-video-and-packaging-design.md`) captured the target state.

## Goals / Non-Goals

**Goals:**
- Single `podcast-reader` CLI entry point that replaces the shell script
- yt-dlp as the generic fallback for any non-YouTube URL
- Clean 6-module Python package with strict typing and tooling
- Preserve all existing behavior for YouTube captions and local/whisper paths
- High-quality developer experience (uv, mypy strict, ruff, pytest markers)

**Non-Goals:**
- Change the HTML output format or chapter generation logic
- Support authenticated yt-dlp downloads in v1 beyond a cookies env var
- Re-implement whisper or youtube-transcript-api — just wrap them

## Decisions

**Package layout (chosen over flat scripts):**
```
src/podcast_reader/
├── cli.py          # argparse + routing + pipeline orchestration
├── youtube.py      # caption fetching (moved from youtube_transcript.py)
├── ytdlp.py        # yt-dlp audio download + title (new)
├── transcribe.py   # whisper-ctranslate2 wrapper (new)
├── chapters.py     # Claude chapter gen (moved)
└── html.py         # styled transcript (moved)
```

**URL routing (centralized in cli.py):**
1. YouTube → youtube.py (no download)
2. Any other http(s) → ytdlp.py → whisper
3. Local file → whisper

**Subprocess isolation:** All external CLIs (yt-dlp, whisper) are invoked via subprocess with explicit arg builders. This keeps the heavy optional deps out of the base package and makes testing easy with mocks.

**Code quality gates (non-negotiable):**
- mypy --strict on src/
- ruff check + format
- pytest -m "not integration" for fast feedback
- Integration tests marked and opt-in

**Build system:** hatchling (simple, PEP 621 native). No setuptools legacy.

## Risks / Trade-offs

- yt-dlp is a large dependency with many transitive packages; kept as required (users doing X/Twitter work need it anyway).
- whisper-ctranslate2 and pyannote are optional extras because of CUDA/torch weight.
- Moving files broke git blame for some history; accepted as cost of proper packaging.
- Shell users had to migrate to the new CLI name — documented in README.

## Migration Plan

1. Land the full Python package (this change).
2. Update all docs, examples, and the published quick-start.
3. (Later) Publish to PyPI when stable.
4. Old `transcribe.sh` and loose .py files deleted from repo root after migration.

## Open Questions (at time of design)

- Exact CLI flag names for model / output-dir (resolved during implementation to match prior env-var + argparse conventions).
- Whether to keep a thin shell wrapper for backwards compat (decided against; clean break).
