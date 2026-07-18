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
# includes torch + pyannote.audio for whisper-ctranslate2 diarization when
# HF_TOKEN is set; chapter generation is built in — bring your own API key)
uv tool install '.[whisper]'
# After upgrading, reinstall if you already had an older whisper-only install:
# uv tool install --force '.[whisper]'
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
| `PODCAST_READER_DATA_DIR` | `~/PodcastReader` | Engine data dir (library, journal, settings, packs: `models/`, `runtime/`, `workers/`, `tools/`) |
| `PODCAST_READER_TOOLS_DIR` | _(none)_ | Preferred directory for external tools; the engine exports it to `<data_dir>/tools` when unset |

## Package Structure

| Module | Purpose |
|--------|---------|
| `src/podcast_reader/cli.py` | Main CLI entry point — URL routing, pipeline orchestration |
| `src/podcast_reader/youtube.py` | Fetch YouTube captions as whisper-compatible JSON |
| `src/podcast_reader/ytdlp.py` | Download audio via yt-dlp; structured `download_failed` (residence-gated `-U` single-retry heal) and `download_auth_required` (neutral message, no retry — each face authors its own cookie hint) |
| `src/podcast_reader/transcribe.py` | Freeze-aware transcribe switch: bundled `whisper-worker` (model-pack validation, cuda→cpu fallback, streamed progress) or whisper-ctranslate2 |
| `src/podcast_reader/workers/whisper_worker.py` | Frozen whisper worker: argv in, ctranslate2-shaped JSON out, `progress` lines on stderr (lazy faster-whisper via the `worker` extra) |
| `src/podcast_reader/workers/diarization_worker.py` | Frozen diarization worker: stdlib WAV in (in-memory waveform, no torchcodec/FFmpeg), `turns.json` out, offline HF cache next to the executable (lazy torch/pyannote via the `diarization` extra) |
| `src/podcast_reader/diarize.py` | Engine diarize step: ffmpeg pre-convert to staged 16 kHz mono WAV, worker spawn from the validated pack, pure-stdlib max-overlap speaker merge, atomic JSON enrichment; warn-don't-fail |
| `src/podcast_reader/tools.py` | Tool resolution (external tools + frozen bundled workers), spawn kwargs, `run_child_streaming` |
| `src/podcast_reader/types.py` | TypedDict boundaries: PipelineRequest/Event, JobRecord, LibraryEntry, EngineSettings; `PipelineError` |
| `src/podcast_reader/pipeline.py` | Shared step runner with progress events (used by CLI and engine) |
| `src/podcast_reader/engine/settings.py` | Data dir, engine state (port/token), user settings persistence |
| `src/podcast_reader/engine/library.py` | Managed transcript library: source-identity keys, atomic index, staged writes |
| `src/podcast_reader/engine/jobs.py` | Persistent job journal, FIFO single-worker execution; publishes into the shared EventBus; jobs may carry rerun `overrides` (per-job model picks) and record the resolved `models` they ran with (both migration-safe on journal load) |
| `src/podcast_reader/engine/events.py` | `EventBus` — public event-publish seam (SSE fan-out) shared by the job store, pack manager, and media manager |
| `src/podcast_reader/engine/packs.py` | Built-in pack registry (pinned CUDA wheels, HF model snapshots, unpublished diarization), manifest types, compat/integrity pure functions |
| `src/podcast_reader/engine/pack_manager.py` | Pack downloads (Range resume, sha256-named staging, fail-closed verify) + `PackManager` installer thread (atomic install, manifest-first uninstall) |
| `src/podcast_reader/engine/hardware.py` | `nvidia-smi` GPU probe (cached) + hardware-derived pack recommendations |
| `src/podcast_reader/engine/media.py` | Floating-player media core (`MediaManager`): source→kind classification (YouTube via `extract_video_id`, local, remote), `ffmpeg`-only probe (no ffprobe), lazy single-flight download (reuses `ytdlp.download_video`) + bounded LRU cache (`.part` staging, partials never served, atomic commit), `media_state`/`media_progress` events (carry `source_id`, never `job_id`) |
| `src/podcast_reader/engine/managed_tools.py` | Bundle tool-seed reconciliation into `<data_dir>/tools` (newer wins), `PODCAST_READER_TOOLS_DIR` export, scheduled yt-dlp self-update |
| `src/podcast_reader/engine/pairing.py` | In-memory pairing-code state: mint (6-char unambiguous alphabet, 300 s TTL, replaces prior), claim (constant-time, single-use, 5-failed-attempt budget, uniform rejection); never persisted or logged |
| `src/podcast_reader/engine/web_session.py` | Stateless signed 180-day browser sessions: bearer-keyed HMAC, expiry, generation revocation, constant-time verification |
| `src/podcast_reader/engine/web_surface.py` | Private-web shell assets and fail-closed transcript CSP: exact script text, order, renderer shape, and legacy tuple compatibility |
| `src/podcast_reader/engine/search.py` | Bounded local-only library search over canonical transcript HTML; strict parsing, newest-first scan, minimized excerpts, partial-result signaling |
| `src/podcast_reader/engine/cookies.py` | Netscape cookie-jar validation (domain suffix-match, 1 MB cap) + storage at `<data_dir>/cookies/<domain>.txt` (atomic 0600, dir 0700); metadata-only listing, delete |
| `src/podcast_reader/engine/app.py` | FastAPI app: bearer auth (exemptions: `POST /v1/pair/claim`, `GET /v1/embed/{id}`), jobs (incl. confirm/dismiss of awaiting-confirmation), events, library, media (`/v1/media/{id}/info` + Range byte-serving), YouTube embed (`/v1/embed/{id}`), settings, keys (push + test), providers, packs (list/install/uninstall), pair (mint/claim), cookies (put/list/delete), health, shutdown routes |
| `src/podcast_reader/engine/embed.py` | Tokenless YouTube embed page served from the loopback http origin (the Error 152/153 fix — a `file://` renderer isn't a valid embedding origin): hosts the YouTube IFrame API + a `pr-embed`/`pr-embed-cmd` postMessage protocol (ready/time/error ↔ seek), validated video id, shared with `app/src/renderer/src/embed-protocol.ts` |
| `src/podcast_reader/engine/process.py` | Pre-bound socket handshake, discovery file, child reaping, `serve`; the job runner merges per-job rerun `overrides` (whisper/chapter model) over the settings snapshot and clears exactly the cached staging artifacts a change invalidates (whisper → re-transcribe keeping audio; chapter-only → re-chapter + re-render); records the resolved `models` (whisper + chapter provider/model) on the job for the UI |
| `src/podcast_reader/engine/serve_guardian.py` | Frozen `serve-guardian` subcommand: pre-bound loopback gate, strict Serve-status proof, foreground child Job Object/POSIX parent-death group, and stdin-EOF lease cleanup; never uses Funnel or receives the engine bearer |
| `spike/` | Packaging spike evidence (PyInstaller onedir prototype, SPIKE_REPORT.md) |
| `packaging/engine.spec` + `build_engine.py` | Production frozen engine onedir: engine + whisper-worker entry points, MERGE/COLLECT, `copy_metadata("podcast-reader")`, ctranslate2/faster_whisper hooks (`hooks/`), tool seeds + flat `tools-manifest.json` into `_internal/tools/` |
| `packaging/frozen_smoke.py` | Shared stdlib-only frozen e2e smoke (boot → handshake → version assert → pack install → fixture transcription); CI and local proof both run it |
| `packaging/diarization.spec` + `build_diarization_pack.py` | Diarization worker pack build (CPU-torch venv per `DIARIZATION_SMOKE.md`, offline community-1 cache, tar.gz + manifest); release job in `pack-diarization.yml` is HF_TOKEN-gated |
| `src/podcast_reader/providers.py` | Built-in chapter LLM provider defaults plus pure effective-registry resolution for validated named providers (base URL, default model, per-name key env, max_tokens); HTTPS-or-loopback and credential-free URL validation |
| `src/podcast_reader/chapters.py` | Generate chapter markers via any registry provider's OpenAI-compatible `/chat/completions`; `verify_key` minimal round-trip backs `POST /v1/keys/test` |
| `src/podcast_reader/html.py` | Styled canonical HTML renderer with chapters/speakers, media sync, and private literal find-within-transcript (keyboard/IME, bounded index, mutation guard) |
| `src/podcast_reader/web_assets/` | Dependency-free private-web pairing, library, library-wide search, and sandboxed transcript Reader shell |
| `scripts/measure-private-search-capacity.ps1` | Aggregate-only real-library size/coverage probe for the shipped private-search scan limits; emits no titles, text, URLs, queries, or paths |
| `scripts/walk_repros.py` + `docs/walk-repros/` | Fail-closed weekly-walk repro ingestion: explicit temporary-artifact quarantine, hashes, durable disposition manifests, prior-implementation failure proof, and hostile controls for integrity-sensitive fixes |
| `scripts/repro.py` + `docs/repro.md` | Root walk/browser proof command: suite discovery, focused Playwright selection, fail-fast prerequisite diagnostics, distinct environment-vs-assertion exits, and the shared CI execution path |
| `pyproject.toml` | Dependencies, entry point, tool configuration |

### Desktop app (`app/` — independent npm package, see `app/README.md`)

| Module | Purpose |
|--------|---------|
| `app/src/main/engine.ts` + `engine-cmd.ts` | Engine supervision: adopt-or-kill via the discovery handshake, three-way spawn chain, sentinel readiness |
| `app/src/main/engine-client.ts` + `sse.ts` | Typed bearer-authed `/v1` client + reconnecting SSE consumer with hydration |
| `app/src/main/engine-manager.ts` + `quit.ts` + `respawn-policy.ts` | Composition root: push-keys-before-ready ordering (shared `wireUp`), status broadcast, quit sequence (abort SSE → `POST /v1/shutdown` → bounded wait → force-kill), and bounded auto-respawn of a crashed **spawned** engine (1s/2s/4s backoff, give-up-after-3, 60s-healthy reset, quit-safe checkpoints, manual `restart()` + the `restarting` status; adopted engines keep prior behavior) |
| `app/src/main/serve-{status,journal,manager,process}.ts` + `private-web.ts` | Explicit tailnet transport: fail-closed status parsing, fsynced pending/active ownership, exact-match reconciliation, packaged guardian protocol, and serialized engine lifecycle integration |
| `app/src/main/vault.ts` | safeStorage-encrypted key vault (session-memory fallback when encryption unavailable) |
| `app/src/main/ipc.ts` + `protocol.ts` | Typed IPC handlers; `podcast-reader://` URL validation (confirm-before-run) |
| `app/src/main/media-protocol.ts` | `app://media/<source_id>` privileged-scheme handler: sha256-id validation (no SSRF/traversal), adds the bearer token (renderer never holds it), forwards `Range`, returns the engine `Response` verbatim (streamed) |
| `app/src/main/external-links.ts` | External-navigation + YouTube-embed policy for the `file://` renderer: `isExternalWebUrl` (http/https → `shell.openExternal`, wired in index.ts via `setWindowOpenHandler`/`will-navigate`) and the host-scoped `Referer` injection that fixes YouTube embed Error 153 (a `file://` origin sends no usable referer) |
| `app/src/main/updater.ts` | electron-updater orchestration: full-download GitHub Releases, consent, engine-quit-before-install; gated off in dev/unsigned |
| `app/src/main/app-config.ts` | App-side config under userData (`first_run_complete` — the setup wizard's gate) |
| `app/src/main/index.ts` | Main entry: lifecycle glue; window creation passes the branded `icon:` resolved packaged (`<resources>/icon.png`) vs dev (`<app>/build/icon.png`) |
| `app/src/preload/index.ts` | contextBridge `window.api` — the credential-free renderer's only door |
| `app/src/renderer/` | Vanilla-TS views (Library/Reader/New/Settings, including named OpenAI-compatible provider CRUD reusing the write-only key flow, + first-run Setup wizard, whose optional "AI model" section reuses the Settings chapter-provider/key flow via `chapter-onboarding.ts` — provider→docs-URL map, custom-URL toggle, putKey/putSettings save plan — and never gates Finish/Skip) + hash router + jobs/packs stores; the New view's job card leads with the video title (the link to the transcript once done), the source URL beneath, a 2-column step/model table (trivial resolve/download hidden, render only on error, plus Transcription + Chapters model rows from the job's recorded `models`), and a "Rerun with a different model" link (a dialog — opt-in re-transcribe and/or regenerate-chapters sections — via `rerun-plan.ts`); a header theme toggle (System/Light/Dark, persisted, `app-theme.ts` + pre-paint inline script in index.html); Reader hosts the inline `media-player.ts` (docked in a left column beside the transcript — hideable via the header ✕ or a permanent toolbar Show/Hide toggle, persisted — stacking on narrow windows) (video/audio via `app://media`; YouTube via an iframe loading the engine's loopback `/v1/embed/{id}` page, `embed-protocol.ts` postMessage sync, with a "Watch on YouTube" browser fallback on embed error) wired to the transcript iframe by `sync-bridge.ts` (`pr-sync`, dual source+channel filter) |
| `app/src/renderer/src/style.css` | Editorial / Reader design system: token-driven (`--serif`/type scale/`--space-*`/`--radius-*`/`--shadow-sm`), warm-paper light + calm dark palettes, system-serif display titles over `system-ui` body, list-led Library, one warm red-brown `--accent` (light `#9a3b2e` / dark `#e0876f`, matching the icon); no bundled font, AA contrast, reduced-motion guarded. Restyle-only — never touches the sandboxed `html.py` artifact |
| `app/src/shared/types.ts` | TS mirrors of the Python boundary types (key-set parity enforced by the e2e integration smoke) |
| `app/tests/mock-engine/` + `app/tests/e2e/` | Scriptable mock engine (separate process, real handshake) + Playwright suites |
| `app/tests/install/walkthrough.mjs` | Installed-app proof driven by `test-installer.yml`: launches the NSIS-installed exe (real frozen engine, nothing mocked), asserts the engine handshake via `getEngineStatus`, captures a submitted YouTube captions job, then installs the pinned tiny Whisper pack and transcribes the short speech fixture through to Reader; screenshots are run artifacts and silent uninstall closes the loop |
| `app/electron-builder.config.cjs` + `app/scripts/dist.mjs` | Packaging: NSIS/dmg+zip, protocol registration, `--engine-dir` extraResources input, branded `build/icon.png` (electron-builder derives `.icns`/`.ico`; runtime window icon shipped via extraResources) |
| `app/scripts/build-icons.mjs` + `app/build/icon.{svg,png}` | Committed icon source + 1024 render; `build-icons` (dev step, not CI) renders via rsvg-convert with PNG magic/dimension asserts |
| `app/src/renderer/src/empty-state.ts` | Branded library empty-state copy + first-transcript CTA href (pure; rendered by `views/library.ts`) |

Engine `/v1` surface the app consumes: `health`, `shutdown`, `jobs` (+
`{id}`, `{id}/confirm`, `DELETE {id}`; `POST jobs` accepts optional rerun
`overrides`), `events` (SSE; job events carry
`data.job_id`, pack events carry `data.pack_id`, media events carry
`data.source_id`, and only job events carry `job_id`), `library`,
`POST search` (private bounded transcript search), `transcripts/{id}.html`,
`media/{id}/info` + `media/{id}` (Range bytes via
the `app://media` proxy), `embed/{video_id}` (tokenless YouTube embed page,
loaded directly by the Reader iframe over the loopback origin — not via
`window.api`; main builds the URL from the engine coordinates), `settings`,
`keys`, `keys/test`, `providers`,
`packs` (+ `POST {id}/install`, `DELETE {id}`), `POST pair` (mint a pairing
code), `cookies` (metadata list + `DELETE {domain}`).

Tailnet browser `/web` surface: public shell/assets at `GET /web/` and
`GET /web/assets/{app.js,app.css}`; same-origin `POST /web/api/pair/claim`;
bearer-plus-same-origin `POST /web/api/session`; signed-cookie
`GET /web/api/library` and `GET /web/api/transcripts/{source_id}.html`; plus
signed-cookie, same-origin `POST /web/api/search` and `POST /web/api/logout`.

### Chrome extension (`extension/` — independent npm package, see `extension/README.md`)

| Module | Purpose |
|--------|---------|
| `extension/public/manifest.json` | MV3 manifest: least-privilege permissions (`storage, alarms, notifications, contextMenus, activeTab`; `host_permissions` only `http://127.0.0.1/*`), `optional_permissions: cookies` + https-only optional hosts, no content scripts, Chrome 120+ |
| `extension/src/popup.ts` | The popup (submission surface — `action.onClicked` never fires with `default_popup`): pairing form, submit-active-tab, hydrate-then-stream progress, cookie capture, protocol fallback; textContent-only DOM (eslint-fenced) |
| `extension/src/sw.ts` | Stateless service worker: context-menu submit + 30 s alarm poll → notifications/badge; never holds an events stream |
| `extension/src/client.ts` + `pairing.ts` + `connection.ts` | Typed engine client (claim/health/jobs/events/cookies), pairing parse + claim→verify→store flow, popup-open connection probe |
| `extension/src/etld.ts` + `capture.ts` + `netscape.ts` | Registrable-domain (eTLD+1) derivation, per-domain capture targeting, Netscape jar serialization (`#HttpOnly_`) |
| `extension/src/storage.ts` + `tracking.ts` + `jobs-view.ts` | `chrome.storage.local` wrapper (`{port, token}` pairing, bounded tracked jobs), submit side-effects, pure view/poll/badge logic |
| `extension/tests/e2e/` | Playwright: real built `dist/` via `--load-extension` + the app's mock engine (pairing, jobs, cookies, engine-down) |
| `extension/scripts/zip.mjs` | Deterministic store-uploadable zip from `dist/` |

Engine `/v1` surface the extension consumes: `POST pair/claim` (the single
unauthenticated route — code → token), `health`, `POST jobs`
(`requires_confirmation: false`), `jobs/{id}`, `events` (fetch-stream),
`PUT cookies`. Shared payload types import from `app/src/shared/types.ts`
(one mirror for both TS consumers — design decision 2).

## Pipeline

1. **YouTube URL** → `youtube.py` fetches captions → whisper JSON
2. **Other URL** → `ytdlp.py` downloads audio → `transcribe.py` runs whisper → whisper JSON
3. **Local file** → `transcribe.py` runs whisper → whisper JSON
4. `diarize.py` → segments enriched with `speaker` labels (engine jobs with the `diarize` setting on and the diarization pack installed; skipped with a warning otherwise — CLI diarization stays whisper-ctranslate2 `--hf_token`)
5. `chapters.py` → `<stem>_chapters.json` (if an API key for the selected chapter provider is available — CLI: the provider's env var; engine: pushed key with env fallback)
6. `html.py` → `<stem>.html` (styled transcript with TOC, key points, pull quotes, speaker attribution when present; per-passage `data-start`/`data-end`, inert-standalone media sync, and private literal find-within-transcript available in standalone artifacts, desktop Reader, and private-web Reader)

Inline media player (engine jobs / desktop app only): the app's Reader fetches `GET /v1/media/{source_id}/info` for the player kind, then plays YouTube via an iframe loading the engine's loopback `GET /v1/embed/{video_id}` page (real http origin, the Error 152/153 fix; "Watch on YouTube" browser fallback on embed error) or local/remote media streamed through the `app://media` → `GET /v1/media/{source_id}` Range proxy (remote sources lazily downloaded into a bounded LRU cache, `EngineSettings.media_cache_max_bytes`, default 5 GiB). The player docks in a left column beside the transcript (which fills the rest of the width at full height; stacks vertically on narrow windows) — never overlapping it; click a passage to seek; the current passage highlights and follows playback.

## Development

- Use `uv` for all Python package management, never raw `pip`.
- Audio files, JSON, and HTML outputs are gitignored — they're generated artifacts.

### Code Quality

```bash
# Primary walk/browser proof (see docs/repro.md; add `all` for every E2E surface)
python3 scripts/repro.py

# Run tests (unit only)
uv run pytest -m "not integration"

# Run all tests including integration
uv run pytest

# Refresh the golden HTML fixtures after intentional renderer changes
# (includes the longform golden measured by app/tests/e2e/artifact-geometry.spec.ts)
uv run python tests/regen_goldens.py

# Verify every captured product-walk artifact has durable evidence or a reasoned discard
uv run python scripts/walk_repros.py verify docs/walk-repros/*.json

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
python3 ../scripts/repro.py app  # build + mock/real-engine Playwright; diagnoses Xvfb
npm run e2e:integration  # real-engine smoke (needs `uv sync --extra dev` at the root)
npm run dist        # electron-builder installers (--engine-dir maps a frozen engine payload)
```

```bash
# Chrome extension (run from extension/; Node >= 24)
npm run typecheck   # tsc --noEmit (src + tests + configs)
npm run lint        # eslint (incl. the textContent-only DOM fence)
npm run test        # vitest unit tests
npm run build       # vite MV3 build → dist/ + deterministic zip
python3 ../scripts/repro.py extension  # build + Playwright; diagnoses Node/browser/Xvfb
```

```bash
# Frozen engine (run from packaging/; PyInstaller is a build tool, not a dep)
uv venv .venv-engine --python 3.10
uv pip install --python .venv-engine/bin/python '..[worker]' pyinstaller
.venv-engine/bin/python build_engine.py                       # → dist/engine/
python3 frozen_smoke.py dist/engine/podcast-reader-engine     # full e2e proof
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
