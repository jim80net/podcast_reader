# Desktop Packaging & Distribution — Design

**Date:** 2026-06-11
**Status:** Approved design, pre-implementation
**Author:** Jim Park, with Claude

## Problem

podcast_reader is a polished personal pipeline (YouTube captions / yt-dlp download /
whisper transcription / Claude chapters / styled HTML reader) that today requires: a
terminal, `uv`, Python 3.10+, knowledge of extras, env-var configuration, an Anthropic
key, an HF token plus accepting pyannote model terms, and ideally an NVIDIA GPU. That
limits it to one user. The goal is to make it usable by people of varying technical
sophistication without losing the power-user path.

## Decisions made during brainstorming

| Question | Decision |
|----------|----------|
| Least technical target user | Can install a desktop app (no terminal, no Python, no manual key files) |
| Platforms (v1) | Windows + macOS |
| Features for the desktop tier | YouTube transcripts, whisper transcription, AI chapters. Diarization bundled as optional download. |
| Chapter LLM access | Bring-your-own key, multi-provider (Anthropic, OpenAI, xAI, OpenRouter, DeepSeek, custom). Hosted-inference resale is a possible future provider entry — out of scope now. |
| v1 input UX | Desktop app window (paste URL / drop file) **plus** a thin Chrome extension that sends the current tab |
| Shell technology | Electron + Python engine sidecar (Approach A; Tauri considered and documented as the lean fallback) |
| Business model | Explicitly out of scope |
| Next conversation after this ships | Floating video player (UI shell must be able to host video — one reason for Chromium/Electron) |

## Architecture

One engine, three faces:

```
┌─────────────┐  ┌──────────────────┐  ┌────────────┐
│ Electron app │  │ Chrome extension │  │ CLI (as-is)│
└──────┬──────┘  └────────┬─────────┘  └─────┬──────┘
       │ spawn + HTTP/SSE │ HTTP (token)      │ direct import
       ▼                  ▼                   ▼
┌──────────────────────────────────────────────────────┐
│ Engine: FastAPI on localhost (podcast-reader serve)  │
│  job model • SSE progress • library index • settings │
│  pipeline: captions | yt-dlp → whisper → chapters →  │
│  html.py reader artifact                             │
└──────────────────────────────────────────────────────┘
```

### Repo shape (monorepo)

```
src/podcast_reader/        # existing package
  engine/                  # NEW: FastAPI app — jobs, events, library, settings
app/                       # NEW: Electron main + renderer, electron-builder config
extension/                 # NEW: Chrome MV3 extension
```

## Components

### Engine (Python, FastAPI + uvicorn)

Refactor `cli._run_pipeline` from print-as-you-go orchestration into a job model with
step-level progress callbacks. Pipeline steps (resolve source → captions or download →
transcribe → chapters → render) emit events consumed over SSE.

API surface (v1, all under `/v1`):

| Endpoint | Purpose |
|----------|---------|
| `POST /jobs` | Start a job from `{url}` or uploaded file; returns job id |
| `GET /jobs/{id}` | Job state: queued / running(step) / done / failed{code, message, hint} |
| `GET /events` | SSE stream of job progress |
| `GET /library` | Completed transcripts (metadata index) |
| `GET /transcripts/{id}.html` | The rendered reader artifact |
| `GET/PUT /settings` | Whisper model/device, paragraph size, storage dir, toggles |

Storage: outputs move from loose files in cwd to a managed library dir
(default `~/PodcastReader/`) with a `library.json` index. The existing
caching-by-artifact behavior (skip download/transcribe/chapters when outputs exist)
carries over unchanged.

Security model:
- Engine binds `127.0.0.1` only, random port, and generates a per-install bearer token.
- The extension obtains the token through a one-time pairing confirmation shown in the
  app window. CORS restricted to the extension origin.
- **API keys never rest in the engine.** Electron stores them via `safeStorage`
  (OS keychain) and passes them per-request in a header. The CLI continues to read
  `ANTHROPIC_API_KEY` (and new provider equivalents) from the environment.

Operational accessibility details:
- **yt-dlp self-update**, independent of app releases (`yt-dlp -U` against the bundled
  standalone binary, on a schedule and on extraction failure). yt-dlp breakage is the
  most likely field failure; this is the mitigation.
- **Cookies from browser**: settings toggle mapping to yt-dlp's
  `--cookies-from-browser chrome|edge|safari`, replacing the `YT_DLP_COOKIES`
  file-path env var for novices (env var stays for the CLI).

### Multi-provider chapters

`chapters.py` is rewritten against OpenAI-compatible `/chat/completions` with a
provider registry: Anthropic (native SDK or compat endpoint), OpenAI, xAI, OpenRouter,
DeepSeek, custom base URL. Each entry: base URL, default model, auth header shape.
Structured output via JSON mode where supported, falling back to the current
prompt-and-parse approach. A future "our hosted inference" option is just another
registry entry — no architectural change.

### Electron app

- **Main process:** spawns the engine (port handshake via stdout), lifecycle
  management, `safeStorage` key vault, `podcast-reader://` protocol registration,
  auto-update via electron-updater against GitHub Releases.
- **Renderer views:**
  - *Library* — transcript cards (title, source, date, duration)
  - *Reader* — renders the engine's HTML artifact in a webview. v1 reuses `html.py`
    output verbatim (zero rework of the already-good reading experience); this view is
    where the future floating video player will live.
  - *New* — paste URL / drop file, step-by-step progress display
  - *Settings* — provider dropdown + key with "test key" button, whisper model/device,
    storage location, diarization toggle, cookies-from-browser toggle
- **Packaging:** electron-builder → NSIS installer (Windows, signed) and notarized dmg
  (macOS). PyInstaller onedir build of the engine. yt-dlp and ffmpeg ship as static
  binaries (LGPL ffmpeg build; license notices in About).

### First-run download manager

The installer stays ~100 MB by deferring heavy artifacts to a first-run wizard with
progress UI:
1. **Whisper runtime** — CUDA libraries on Windows with NVIDIA GPU detected; CPU/int8
   otherwise (ctranslate2 on Apple Silicon runs CPU int8).
2. **Whisper model** — recommended default by hardware (large-v3 on GPU, small/medium
   on CPU), changeable in settings.
3. **Diarization pack (optional)** — pyannote weights from our own mirror. Licensing
   verified 2026-06-11: speaker-diarization-3.1 + segmentation-3.0 are MIT (HF gating
   is an access form, not a license term); community-1 is CC-BY-4.0 with better
   accuracy. Pick at implementation time after a quality/size spike; attribution in
   the About box either way. No HF account or token involved; `HF_TOKEN` remains as a
   CLI power-user override.

Downloads are resumable, checksummed, and re-runnable from Settings.

### Chrome extension (MV3)

Toolbar button, active on YouTube/X pages (and any page via context menu):
- App running → `POST /v1/jobs` with the tab URL; popup shows SSE progress; "Open in
  app" on completion.
- App not running → navigate to `podcast-reader://transcribe?url=...`, which launches
  the app and queues the job.
- First use → pairing flow: extension opens the app's pairing screen, user confirms,
  token stored in `chrome.storage.local`.

## Error handling

Jobs fail into a structured `{code, message, hint}` where `hint` is user-actionable
("This X post requires login — enable 'Use browser cookies' in Settings", "No API key
configured — chapters were skipped"). Chapters failure degrades gracefully to a
transcript without chapters (matching current CLI behavior). The app's error surface
includes a "save diagnostic bundle" button (engine log + job record, no keys).

## Testing

- **Engine:** existing pytest suite carries over; new API-level tests via FastAPI's
  TestClient with subprocess boundaries mocked (current convention). Job-model unit
  tests for step transitions and failure mapping.
- **App:** Playwright e2e for renderer views against a mock engine; smoke test that a
  real spawned engine handshakes.
- **Extension:** kept slim in v1 — manual test matrix plus a Playwright run with the
  extension loaded.
- **CI:** existing lint/type/test gates; add engine-API job. Installer builds run on
  tag pipelines (Windows + macOS runners) — signing certs are a prerequisite task.

## Phasing

Each phase is an independent openspec change and PR, in order:

1. **Engine extraction** — `engine/` package, job model, SSE, library index;
   pure Python. CLI gains `serve` subcommand.
2. **Multi-provider chapters** — provider registry, per-request key, env-var
   compatibility for the CLI.
3. **Electron MVP** — app shell, four views, engine spawn, installers, signing,
   auto-update.
4. **First-run download manager** — runtime/model/diarization packs, hardware
   detection.
5. **Extension + pairing + protocol handler.**

Afterwards: floating video player design conversation (tracked separately).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| yt-dlp breakage in the field | Self-update independent of app releases; structured error hints |
| PyInstaller + ctranslate2/CUDA packaging fragility | Phase-4 spike before committing to exact runtime-pack layout; CPU path is the guaranteed fallback |
| Code signing / notarization logistics | Treated as explicit phase-3 prerequisite tasks, not afterthoughts |
| Engine port/token drift between app and extension | Single source of truth: app brokers pairing; extension stores port+token from pairing and falls back to the `podcast-reader://` launch when the engine is unreachable |
| Scope creep toward the video player | Out of scope here; Reader view is designed to host it later |

## Out of scope (v1)

Business model / payments, hosted inference, Linux packaging, Firefox/Safari
extensions, in-app video playback, library search/tagging, transcript editing.
