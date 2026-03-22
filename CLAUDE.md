# podcast_reader

Transcribe podcast audio or YouTube videos to styled HTML transcripts. Uses [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) for audio files and [youtube-transcript-api](https://pypi.org/project/youtube-transcript-api/) for YouTube (fetches existing captions, no download).

## Quick Start

```bash
# Transcribe from a YouTube video (uses existing captions, no download)
./transcribe.sh https://www.youtube.com/watch?v=VIDEO_ID "Episode Title"

# Transcribe from a URL
./transcribe.sh https://example.com/episode.mp3 "Episode Title"

# Transcribe a local file
./transcribe.sh ~/Downloads/episode.mp3 "Episode Title"
```

## Setup

Requires: Python 3.10+, `uv`, NVIDIA GPU (optional, falls back to CPU).

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

For speaker diarization, set `HF_TOKEN` and accept model terms at:
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/segmentation-3.0

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3` | Whisper model size |
| `WHISPER_LANG` | `en` | Language code |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `HF_TOKEN` | _(none)_ | HuggingFace token for diarization |
| `ANTHROPIC_API_KEY` | _(none)_ | Enables chapter generation via Claude |
| `SENTENCES` | `5` | Sentences per paragraph in HTML |

## Files

| File | Purpose |
|------|---------|
| `transcribe.sh` | Main entry point — download, transcribe, chapters, convert |
| `generate_chapters.py` | Send transcript to Claude, get chapters with abstracts, key points, and pull quotes |
| `json_to_html.py` | Convert whisper JSON to styled HTML with chapters TOC, key points gutter, and pull quotes |
| `youtube_transcript.py` | Fetch YouTube captions as whisper-compatible JSON |
| `requirements.txt` | Python dependencies |

## Pipeline

1. `whisper-ctranslate2` → `<name>.json` (segments with timestamps)
2. `generate_chapters.py` → `<name>_chapters.json` (chapters, abstracts, types, key points, pull quotes)
3. `json_to_html.py --chapters` → `<name>.html` (styled transcript with TOC, key points in right gutter, pull quotes inline)

For YouTube URLs, step 1 uses `youtube_transcript.py` (fetches existing captions) instead of whisper-ctranslate2. No audio download or API key is required.

Step 2 requires `ANTHROPIC_API_KEY`. Without it, HTML is generated without chapters.

## Development

- Use `uv` for all Python package management, never raw `pip`.
- Audio files, JSON, and HTML outputs are gitignored — they're generated artifacts.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **podcast_reader** (54 symbols, 111 relationships, 3 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/podcast_reader/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/podcast_reader/context` | Codebase overview, check index freshness |
| `gitnexus://repo/podcast_reader/clusters` | All functional areas |
| `gitnexus://repo/podcast_reader/processes` | All execution flows |
| `gitnexus://repo/podcast_reader/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## CLI

- Re-index: `npx gitnexus analyze`
- Check freshness: `npx gitnexus status`
- Generate docs: `npx gitnexus wiki`

<!-- gitnexus:end -->
