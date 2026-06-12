# podcast_reader

Transcribe podcast audio, YouTube videos, or X/Twitter videos to styled HTML transcripts. Uses [whisper-ctranslate2](https://github.com/Softcatala/whisper-ctranslate2) for audio files, [youtube-transcript-api](https://pypi.org/project/youtube-transcript-api/) for YouTube (fetches existing captions), and [yt-dlp](https://github.com/yt-dlp/yt-dlp) for X/Twitter and other platforms.

## Quick Start

```bash
# Transcribe from a YouTube video (uses existing captions, no download)
podcast-reader https://www.youtube.com/watch?v=VIDEO_ID "Episode Title"

# Transcribe from X/Twitter (downloads audio via yt-dlp)
podcast-reader https://x.com/user/status/123456 "Post Title"

# Transcribe from any yt-dlp-supported URL
podcast-reader https://vimeo.com/123456 "Video Title"

# Transcribe a local file
podcast-reader ~/Downloads/episode.mp3 "Episode Title"

# Specify output directory
podcast-reader --output-dir ./output https://example.com/video

# Start the localhost engine (job API + SSE + managed library)
podcast-reader serve
```

## Setup

Requires: Python 3.10+, `uv`, NVIDIA GPU (optional, falls back to CPU).

```bash
# Development
uv sync --extra dev

# Run directly
uv run podcast-reader <url-or-file> [title]

# Install as standalone tool (whisper extra needed for non-YouTube sources;
# chapter generation is built in — bring your own API key)
uv tool install '.[whisper]'
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
| `ANTHROPIC_API_KEY` | _(none)_ | Chapter key for the default `anthropic` provider |
| `<PROVIDER>_API_KEY` | _(none)_ | Chapter key per `--provider`: `OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` |
| `PODCAST_READER_CUSTOM_PROVIDER_URL` | _(none)_ | Base URL for `--provider custom` (https, or http on localhost) |
| `PODCAST_READER_CUSTOM_PROVIDER_KEY` | _(none)_ | API key for `--provider custom` |
| `SENTENCES` | `5` | Sentences per paragraph in HTML |
| `YT_DLP_COOKIES` | _(none)_ | Path to cookies file for authenticated yt-dlp downloads |

## Package Structure

| Module | Purpose |
|--------|---------|
| `src/podcast_reader/cli.py` | Main CLI entry point — URL routing, pipeline orchestration |
| `src/podcast_reader/youtube.py` | Fetch YouTube captions as whisper-compatible JSON |
| `src/podcast_reader/ytdlp.py` | Download audio from X/Twitter and other platforms via yt-dlp |
| `src/podcast_reader/transcribe.py` | Run whisper-ctranslate2 on audio files |
| `src/podcast_reader/tools.py` | Tool resolution (external tools + frozen bundled workers) and spawn kwargs |
| `src/podcast_reader/types.py` | TypedDict boundaries: PipelineRequest/Event, JobRecord, LibraryEntry, EngineSettings |
| `src/podcast_reader/pipeline.py` | Shared step runner with progress events (used by CLI and engine) |
| `src/podcast_reader/engine/settings.py` | Data dir, engine state (port/token), user settings persistence |
| `src/podcast_reader/engine/library.py` | Managed transcript library: source-identity keys, atomic index, staged writes |
| `src/podcast_reader/engine/jobs.py` | Persistent job journal, FIFO single-worker execution; publishes into the shared EventBus |
| `src/podcast_reader/engine/events.py` | `EventBus` — public event-publish seam (SSE fan-out) shared by the job store and pack manager |
| `src/podcast_reader/engine/packs.py` | Built-in pack registry (pinned CUDA wheels, HF model snapshots, unpublished diarization), manifest types, compat/integrity pure functions |
| `src/podcast_reader/engine/pack_manager.py` | Pack downloads (Range resume, sha256-named staging, fail-closed verify) + `PackManager` installer thread (atomic install, manifest-first uninstall) |
| `src/podcast_reader/engine/hardware.py` | `nvidia-smi` GPU probe (cached) + hardware-derived pack recommendations |
| `src/podcast_reader/engine/app.py` | FastAPI app: bearer auth, jobs (incl. confirm/dismiss of awaiting-confirmation), events, library, settings, keys (push + test), providers, packs (list/install/uninstall), health, shutdown routes |
| `src/podcast_reader/engine/process.py` | Pre-bound socket handshake, discovery file, child reaping, `serve` |
| `spike/` | Packaging spike evidence (PyInstaller onedir prototype, SPIKE_REPORT.md) |
| `src/podcast_reader/providers.py` | Chapter LLM provider registry (base URL, default model, key env, max_tokens) + custom-URL validation |
| `src/podcast_reader/chapters.py` | Generate chapter markers via any registry provider's OpenAI-compatible `/chat/completions`; `verify_key` minimal round-trip backs `POST /v1/keys/test` |
| `src/podcast_reader/html.py` | Convert whisper JSON to styled HTML with TOC, key points, pull quotes |
| `pyproject.toml` | Dependencies, entry point, tool configuration |

### Desktop app (`app/` — independent npm package, see `app/README.md`)

| Module | Purpose |
|--------|---------|
| `app/src/main/engine.ts` + `engine-cmd.ts` | Engine supervision: adopt-or-kill via the discovery handshake, three-way spawn chain, sentinel readiness |
| `app/src/main/engine-client.ts` + `sse.ts` | Typed bearer-authed `/v1` client + reconnecting SSE consumer with hydration |
| `app/src/main/engine-manager.ts` + `quit.ts` | Composition root: push-keys-before-ready ordering, status broadcast, quit sequence (abort SSE → `POST /v1/shutdown` → bounded wait → force-kill) |
| `app/src/main/vault.ts` | safeStorage-encrypted key vault (session-memory fallback when encryption unavailable) |
| `app/src/main/ipc.ts` + `protocol.ts` | Typed IPC handlers; `podcast-reader://` URL validation (confirm-before-run) |
| `app/src/main/updater.ts` | electron-updater orchestration: full-download GitHub Releases, consent, engine-quit-before-install; gated off in dev/unsigned |
| `app/src/preload/index.ts` | contextBridge `window.api` — the credential-free renderer's only door |
| `app/src/renderer/` | Vanilla-TS views (Library/Reader/New/Settings) + hash router + jobs store |
| `app/src/shared/types.ts` | TS mirrors of the Python boundary types (key-set parity enforced by the e2e integration smoke) |
| `app/tests/mock-engine/` + `app/tests/e2e/` | Scriptable mock engine (separate process, real handshake) + Playwright suites |
| `app/electron-builder.config.cjs` + `app/scripts/dist.mjs` | Packaging: NSIS/dmg+zip, protocol registration, `--engine-dir` extraResources input |

Engine `/v1` surface the app consumes: `health`, `shutdown`, `jobs` (+
`{id}`, `{id}/confirm`, `DELETE {id}`), `events` (SSE; job events carry
`data.job_id`, pack events carry `data.pack_id` and never `job_id`),
`library`, `transcripts/{id}.html`, `settings`, `keys`, `keys/test`,
`providers`, `packs` (+ `POST {id}/install`, `DELETE {id}`).

## Pipeline

1. **YouTube URL** → `youtube.py` fetches captions → whisper JSON
2. **Other URL** → `ytdlp.py` downloads audio → `transcribe.py` runs whisper → whisper JSON
3. **Local file** → `transcribe.py` runs whisper → whisper JSON
4. `chapters.py` → `<stem>_chapters.json` (if an API key for the selected chapter provider is available — CLI: the provider's env var; engine: pushed key with env fallback)
5. `html.py` → `<stem>.html` (styled transcript with TOC, key points, pull quotes)

## Development

- Use `uv` for all Python package management, never raw `pip`.
- Audio files, JSON, and HTML outputs are gitignored — they're generated artifacts.

### Code Quality

```bash
# Run tests (unit only)
uv run pytest -m "not integration"

# Run all tests including integration
uv run pytest

# Type checking (strict mode)
uv run mypy src/

# Lint and format
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

```bash
# Desktop app (run from app/; Node >= 24)
npm run typecheck   # tsc --noEmit (node + web + e2e projects)
npm run lint        # eslint
npm run test        # vitest unit tests
npm run build       # electron-vite production build into out/
npm run e2e         # Playwright vs the mock engine (build first; xvfb-run -a on headless)
npm run e2e:integration  # real-engine smoke (needs `uv sync --extra dev` at the root)
npm run dist        # electron-builder installers (--engine-dir maps a frozen engine payload)
```

- **mypy**: strict mode, all functions fully typed
- **ruff**: line-length 100, rules E/F/W/I/N/UP/B/A/SIM/TCH
- **pytest**: equality matchers preferred, subprocess mocked in unit tests, integration tests marked with `@pytest.mark.integration`

### Change History (OpenSpec)

Historical feature work is captured as OpenSpec changes under `openspec/changes/archive/`. The two major backported changes are:
- `2026-03-22-youtube-transcript-support`
- `2026-03-24-x-video-and-packaging`

Use `openspec list --archived`, `openspec show`, or browse the archive directories for proposal / design / tasks / specs.

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
