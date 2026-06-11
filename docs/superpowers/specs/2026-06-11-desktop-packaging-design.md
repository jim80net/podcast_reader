# Desktop Packaging & Distribution — Design

**Date:** 2026-06-11
**Status:** Approved design v2, pre-implementation
**Author:** Jim Park, with Claude
**Review history:** v1 approved in brainstorm; v2 applies systems-review findings F1–F13
(frozen-bundle/runtime split, cookie strategy, pairing/discovery, MV3 lifecycle, fault
isolation, self-update layout, concurrency, key handling, protocol hardening, ffmpeg
licensing, handshake, pack versioning). F8's CLI-journal proposal simplified: engine is
sole library writer; CLI keeps writing loose files.

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
| Features for the desktop tier | YouTube transcripts, whisper transcription, AI chapters. Diarization via a separately downloaded worker pack (see First-run section; cut-line applies). |
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
| `GET /jobs/{id}` | Job state: queued / awaiting-confirmation / running(step) / done / interrupted / failed{code, message, hint} |
| `GET /events` | SSE stream of job progress (header auth only — see Security) |
| `GET /library` | Completed transcripts (metadata index) |
| `GET /transcripts/{id}.html` | The rendered reader artifact |
| `GET/PUT /settings` | Whisper model/device, paragraph size, storage dir, toggles |
| `GET /health` | Liveness + version + token fingerprint check |

**Process & discovery model** *(per F3, F7, F12)*: the engine binds `127.0.0.1` on a
random port and writes a discovery file (`<userData>/engine.json`, mode 0600:
`{port, pid, token_fingerprint, version}`) whose path it receives via argv; it then
prints a single ready sentinel. The app watches the file rather than parsing uvicorn
stdout. On startup the app probes any existing discovery file (`GET /health` with
token): adopt a live engine, or kill the stale PID — never two engines. The engine
owns its children (whisper, yt-dlp) via a Windows Job Object with
kill-on-job-close / a POSIX process group, so engine death reaps GPU jobs. Jobs found
`running` at engine startup are marked `interrupted` with a retry affordance.

**Storage** *(per F8)*: outputs move from loose files in cwd to a managed library dir
(default `~/PodcastReader/`) with a `library.json` index. The engine is the **sole
writer** of the index (temp-file + atomic rename). Library entries are keyed by source
identity (URL, or content hash for local files), not bare filename stem, so two
different `episode.mp3` files cannot collide. The existing skip-if-artifact-exists
caching carries over, hardened: artifact writes are temp + atomic rename, and a cache
hit re-validates (JSON parses, HTML non-empty) — a corrupt artifact is a cache *miss*,
not a permanent crash loop. The CLI's one-shot mode keeps writing loose files to cwd
exactly as today; the app offers "import a folder" rather than the CLI writing to the
index.

**Security model** *(per F4, F9)*:
- Engine binds `127.0.0.1` only, with a per-install bearer token. All endpoints
  require `Authorization` headers — no token-in-query fallback (uvicorn access logs
  would capture it). Progress streaming uses `fetch()` + ReadableStream (which can
  send headers), not `EventSource`.
- **API keys never touch disk on the engine side.** Electron stores them encrypted via
  `safeStorage` (OS keychain) and pushes decrypted keys into engine *memory* over the
  token-authed localhost channel at engine start and on settings change. This serves
  jobs from all three faces — including extension-initiated jobs, which carry no key
  of their own. The CLI continues to read provider keys from the environment.

**Fault isolation** *(per F5)*: the chapters step is explicitly fault-isolated — any
provider error (truncation, malformed JSON, network, missing key) is caught, recorded
on the job as a structured warning, and the pipeline proceeds to render a chapterless
transcript. Note: the *current* CLI does not have this property (`cli.py` lets
`generate_chapters` exceptions kill the run before HTML is written); Phase 1 includes
fixing the CLI to the same contract.

Operational accessibility details:
- **Tools live in user data** *(per F6)*: installed binaries (yt-dlp, ffmpeg, ffprobe)
  are immutable seeds; first run copies them to `<userData>/tools/`, and the engine's
  freeze-aware `resolve_tool` prefers that directory. `yt-dlp -U` runs against the
  user-data copy (supported for release binaries), on a schedule and on extraction
  failure — never against the signed install dir (which would break the macOS
  signature seal and be non-writable under Program Files). After an app update, the
  newer of {bundled seed, user copy} wins.
- **Authenticated content (X, member-only)** *(per F2 — replaces v1's
  cookies-from-browser plan)*: `--cookies-from-browser chrome` is permanently broken
  on Windows since Chrome 127's App-Bound Encryption (yt-dlp #10927), so it cannot be
  the novice path. Primary mechanism: **extension-assisted cookie capture** — on an
  auth-required failure, the app prompts the user to grant the extension optional
  `cookies` permission for the target domain; the extension reads cookies via
  `chrome.cookies.getAll` and POSTs a Netscape-format jar to the engine (token-authed,
  stored 0600 in user data), feeding the existing `--cookies` path. Secondary:
  `--cookies-from-browser firefox`, and `safari` behind a macOS Full-Disk-Access
  priming dialog. Power users keep file import (`YT_DLP_COOKIES` stays for the CLI).
  Error hints are per-platform and never recommend the broken Chrome/Windows path.

### Multi-provider chapters

`chapters.py` is rewritten against OpenAI-compatible `/chat/completions` with a
provider registry: Anthropic (native SDK or compat endpoint), OpenAI, xAI, OpenRouter,
DeepSeek, custom base URL. Each entry: base URL, default model, auth header shape.
Structured output via JSON mode where supported, falling back to the current
prompt-and-parse approach. A future "our hosted inference" option is just another
registry entry — no architectural change.

### Electron app

- **Main process:** spawns the engine (discovery-file handshake, above), lifecycle
  management with an explicit quit sequence *(per F6, F7)* — signal engine → engine
  terminates children → wait → exit/`quitAndInstall` — `safeStorage` key vault,
  `podcast-reader://` protocol registration, auto-update via electron-updater against
  GitHub Releases. Differential updates degrade badly with large changing binaries
  (electron-builder #6265), so releases either mark the engine dir as uncompressed
  extraResources or accept full-download updates — decided during the Phase 1 spike.
- **Renderer views:**
  - *Library* — transcript cards (title, source, date, duration)
  - *Reader* — renders the engine's HTML artifact in a webview. v1 reuses `html.py`
    output verbatim (zero rework of the already-good reading experience); this view is
    where the future floating video player will live.
  - *New* — paste URL / drop file, step-by-step progress display; also surfaces
    protocol-initiated jobs for confirmation *(per F10)*
  - *Settings* — provider dropdown + key with "test key" button, whisper model/device,
    storage location, diarization pack management, cookie management, re-run setup
- **Packaging:** electron-builder → NSIS installer (Windows, signed) and notarized dmg
  (macOS). PyInstaller onedir build of the engine. ffmpeg + ffprobe ship as separate
  executables invoked via subprocess *(per F11)* — mere aggregation, not linking:
  BtbN LGPL build, or a GPL build with a source offer; comply with the ffmpeg legal
  checklist including exact-source availability and no reverse-engineering clause in
  the EULA; license texts and attribution in About.

### Frozen-bundle / downloadable-pack split *(per F1 — supersedes v1's "first-run download manager")*

A PyInstaller-frozen app cannot acquire Python packages post-install; only shared
libraries, executables, and data files can be downloaded later. The split is
therefore:

**In the frozen engine bundle (ships in the installer):**
- Python runtime, FastAPI engine, faster-whisper + ctranslate2 (the same wheel serves
  CPU and CUDA — it dlopens cuBLAS/cuDNN at runtime if present), tokenizers.
- Transcription moves from shelling out to the `whisper-ctranslate2` console script to
  a **whisper worker subprocess built as a second entry point in the same onedir
  bundle** — preserving today's crash/VRAM isolation (`transcribe.py`'s subprocess
  boundary) without needing pip at runtime. Prior art: Purfview/whisper-standalone-win.
- `resolve_tool` is rewritten freeze-aware: under `sys.frozen`, resolve against the
  bundle's tools directory and `<userData>/tools/`, never `Path(sys.executable).parent`.
- yt-dlp + ffmpeg + ffprobe standalone binaries as seeds (copied to user data, F6).

**Downloadable packs (first-run wizard, resumable, checksummed, re-runnable from
Settings):**
1. **CUDA libraries** (Windows + NVIDIA only): cuBLAS/cuDNN shared libraries repacked
   from the `nvidia-*-cu12` wheels (verify redistribution against the CUDA EULA redist
   list during the spike), unpacked to `<userData>/runtime/` and added to the DLL
   search path before model load. The ctranslate2↔cuDNN version pairing is strict and
   pinned.
2. **Whisper model weights**: recommended by detected hardware (large-v3 ≈ 3 GB on
   GPU; small/medium on CPU; Apple Silicon runs CPU int8), changeable in settings.
3. **Diarization worker pack (optional)**: a *separate frozen worker* (pyannote.audio
   + torch, CPU build ~1.5–2.5 GB) plus a pre-seeded local HF cache containing the
   full pipeline (segmentation + embedding models — weights alone are insufficient,
   and pipeline loading is offline via local paths, no HF account). Licensing verified
   2026-06-11: pyannote 3.1 weights MIT; community-1 CC-BY-4.0; attribution in About.
   **Cut-line:** if the Phase 1 spike shows the worker pack is not viable in
   reasonable size/effort, desktop diarization slips to post-v1 and remains CLI-only
   without blocking release.

**Pack versioning** *(per F13)*: every pack carries a manifest
`{pack_schema, component_versions, compat_range}`; engine startup validates
compatibility (e.g., ctranslate2 vs cuDNN) and the wizard re-downloads deltas when an
app update moves the compat range.

**Installer size**: ~150–200 MB realistic (Electron + frozen engine + seeds), not the
v1 doc's ~100 MB.

### Chrome extension (MV3) *(per F3, F4, F10)*

Toolbar button, active on YouTube/X pages (and any page via context menu):
- **Progress model:** job state is the source of truth, not the stream. Popup-open:
  `fetch()` + ReadableStream with `Authorization` header renders live progress.
  Popup closed/reopened: hydrate from `GET /jobs/{id}`, then re-attach. Background
  completion notification via `chrome.alarms` polling (30 s floor) — no reliance on
  long-lived SSE in the MV3 service worker (terminated at ~30 s idle; the Chrome 116
  lifetime extension covers WebSockets, not SSE).
- **Discovery & re-pairing:** extension stores `{port, token}` from pairing. On
  connection failure it triggers `podcast-reader://reconnect` once; the running app
  answers with a silent re-handshake (token unchanged, port refreshed). Pairing
  remains a one-time user confirmation in the app window.
- **Protocol hardening:** `podcast-reader://transcribe?url=...` never auto-executes.
  Protocol-initiated jobs land in `awaiting-confirmation`, rendered in the app's New
  view with the URL shown; one click runs. Scheme/host shape validated before display.
- **Cookie capture flow** as described under Authenticated content (optional
  permission, on demand, per domain).

## Error handling

Jobs fail into a structured `{code, message, hint}` where `hint` is user-actionable
and **per-platform** *(per F2)* — e.g., on Windows: "This X post requires login —
click 'Grant access' to let the extension share your X login with Podcast Reader",
never "enable browser cookies." Chapters failures degrade to a chapterless transcript
with a visible warning *(per F5)*. Interrupted jobs (engine/app crash) are marked and
retryable *(per F7)*. The app's error surface includes a "save diagnostic bundle"
button (engine log + job record, no keys, no cookies).

## Testing *(expanded per review §5)*

- **Engine:** existing pytest suite carries over; new API-level tests via FastAPI's
  TestClient with subprocess boundaries mocked (current convention). Job-model unit
  tests for step transitions, failure mapping, and chapters fault isolation (engine
  *and* CLI). Cache-corruption tests: truncated artifact ⇒ cache miss, not crash loop.
  Concurrency test for atomic index writes. SSE auth test: header accepted,
  token-in-query rejected.
- **Frozen-artifact smoke test in CI** (the critical addition): build the PyInstaller
  onedir on Windows + macOS tag runners, boot it, complete the discovery handshake,
  and transcribe a 5-second fixture WAV on CPU end-to-end. Nothing in the
  mocked-subprocess test convention catches frozen-runtime breakage otherwise.
- **Tool resolution under freeze:** unit tests for freeze-aware `resolve_tool`
  (frozen vs not; seed vs user-data precedence; post-update newer-wins).
- **Lifecycle:** kill-engine-mid-job ⇒ children reaped (Job Object / process group),
  job `interrupted`, no corrupt cache hit on restart. Engine restart ⇒ extension
  recovers via re-pairing flow.
- **App:** Playwright e2e for renderer views against a mock engine; smoke test that a
  real spawned engine handshakes. Update-while-running test on a Windows runner
  (quit sequence releases file locks before `quitAndInstall`).
- **Extension:** Playwright with extension loaded: pairing, progress hydration after
  popup reopen, cookie-capture flow against a mock engine.

## Phasing *(re-sequenced per F1)*

Each phase is an independent openspec change and PR, in order:

1. **Engine extraction + packaging spike** — `engine/` package, job model, SSE,
   library index, discovery file, process-group child management, chapters fault
   isolation (engine + CLI); CLI gains `serve`. **In parallel, the
   PyInstaller/ctranslate2/CUDA spike** — it determines the transcription invocation
   (frozen whisper worker), freeze-aware `resolve_tool`, and the diarization
   cut-line decision, all of which shape the engine API. The spike is on Phase 1's
   critical path, not Phase 4's.
2. **Multi-provider chapters** — provider registry, keys-in-memory push channel,
   env-var compatibility for the CLI.
3. **Electron MVP** — app shell, four views, engine spawn/adopt/quit sequence,
   installers, signing, auto-update strategy fixed (extraResources vs full-download).
4. **Download manager** — CUDA pack, model packs, pack manifests/compat validation,
   tools seeding + yt-dlp self-update; diarization worker pack if the spike said go.
5. **Extension** — pairing/re-pairing, progress model, protocol confirmation flow,
   cookie capture.

Afterwards: floating video player design conversation (tracked separately).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| yt-dlp breakage in the field | Self-update of the user-data copy, independent of app releases; structured per-platform error hints |
| PyInstaller + ctranslate2/CUDA packaging fragility | Spike moved to Phase 1 critical path; CPU path is the guaranteed fallback; frozen smoke test in CI |
| Diarization worker pack too heavy | Explicit cut-line: ships post-v1, CLI-only meanwhile |
| Cookie capture UX confuses users | One-click grant flow via extension; per-platform hints; file import always available |
| Code signing / notarization logistics | Treated as explicit phase-3 prerequisite tasks, not afterthoughts |
| Engine port/token drift between app and extension | Discovery file + `podcast-reader://reconnect` re-handshake; app adopts-or-kills stale engines |
| Orphaned GPU jobs / double engines | Job Object / process group child reaping; health-probe + PID adoption on startup |
| Pack/app version skew | Pack manifests with compat ranges validated at engine startup |
| Scope creep toward the video player | Out of scope here; Reader view is designed to host it later |

## Out of scope (v1)

Business model / payments, hosted inference, Linux packaging, Firefox/Safari
extensions, in-app video playback, library search/tagging, transcript editing,
background (service-worker-resident) progress streaming in the extension.
