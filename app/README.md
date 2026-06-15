# podcast-reader desktop app

Electron shell for the podcast-reader engine. The app supervises a local
engine process over the Phase 1 discovery handshake and talks to it over the
bearer-authenticated `/v1` API; the renderer is credential-free (all engine
HTTP/SSE happens in the main process and crosses to the UI over typed IPC).

## Development

Requires Node >= 20 and (for the dev engine posture) the repo's Python
toolchain (`uv sync --extra dev` at the repo root).

```bash
cd app
npm install
npm run dev        # boots the app; spawns the engine via the dev fallback
npm run test       # vitest unit tests
npm run typecheck  # tsc --noEmit (node + web + e2e projects)
npm run lint       # eslint
npm run build      # electron-vite production build into out/
npm run e2e        # Playwright e2e against the mock engine (build first)
npm run dist       # build + electron-builder installers (see Packaging)
```

## End-to-end tests

The Playwright suite launches the BUILT app (`npm run build` first; on
headless hosts wrap in `xvfb-run -a`):

```bash
npm run build
xvfb-run -a npx playwright test                 # both projects
npm run e2e                                     # mock-engine project only
npm run e2e:integration                         # real-engine smoke only
```

- **`e2e` project** — runs against the mock engine
  (`tests/mock-engine/server.ts`, a separate child process): the fixture
  writes `engine-state.json` + `engine.json` into a temp
  `PODCAST_READER_DATA_DIR`, so the app ADOPTS the mock through its
  production discovery path. The mock honors the handshake to the point of
  exiting on `POST /v1/shutdown`, which is how the quit-sequence ordering
  (events stream closed → shutdown POST → engine exit, per P1) is asserted
  from its persisted log.
- **`integration` project** — the real-engine smoke: spawns
  `uv run podcast-reader serve` via the dev fallback (requires
  `uv sync --extra dev` at the repo root) and asserts exact key-set equality
  of live `JobRecord`/`LibraryEntry`/`EngineSettings` payloads against the
  `src/shared/types.ts` mirrors, so mirror drift fails CI.

Tests isolate state per run via `PODCAST_READER_DATA_DIR` and
`PODCAST_READER_USER_DATA_DIR` (the latter overrides Electron's userData —
vault location and single-instance lock scope).

## Packaging (design decisions 9, 10)

`electron-builder.config.cjs` defines NSIS per-user (Windows), dmg + zip
(macOS — the zip is what electron-updater consumes), and a `--linux dir`
target that exists purely to prove the packaging pipeline on Linux hosts/CI.
Installer-level `podcast-reader://` protocol registration rides in the same
config.

```bash
npm run dist -- --win                            # unsigned NSIS installer
npm run dist -- --mac                            # unsigned dmg + zip
npm run dist -- --linux dir                      # pipeline proof, not a ship target
npm run dist -- --engine-dir ../packaging/dist/engine --win   # with the frozen engine
```

`--engine-dir` (handled by `scripts/dist.mjs`) maps the frozen engine onedir
(`podcast-reader-engine[.exe]`, sibling `whisper-worker`, shared
`_internal/` incl. the tool seeds) UNCOMPRESSED into `<resources>/engine/`
as extraResources — executables cannot run from inside the asar archive.
The payload is real now: build it with `packaging/build_engine.py` (see
`packaging/engine.spec`; the CI `frozen-smoke` job proves the same build
end-to-end on ubuntu + windows). Builds without `--engine-dir` are valid:
the app falls back to the spawn chain above.

## Icon / branding

The app icon (the "play + transcript lines" mark) lives as a single committed
source: `build/icon.svg` and a rendered `build/icon.png` (1024×1024). Only those
two are committed — **electron-builder 26 derives the platform `.icns` (macOS)
and `.ico` (Windows/NSIS) from `build/icon.png` at packaging time**, so no
`.icns`/`.ico` are hand-generated or committed, and CI needs neither
`rsvg-convert` nor ImageMagick.

```bash
npm run build-icons   # re-render build/icon.png from build/icon.svg (dev step)
```

`build-icons` (`scripts/build-icons.mjs`) spawns `rsvg-convert` and asserts the
output is a valid 1024×1024 PNG. It is a **documented dev step, not a build/CI
dependency** (`icon.png` is committed) — run it only when the mark changes or a
designer drops in a replacement 1024px source. The per-platform `icon` fields in
`electron-builder.config.cjs` point at `build/icon.png` explicitly; the runtime
`BrowserWindow` icon ships via extraResources (`<resources>/icon.png` packaged,
`build/icon.png` in dev). No signing/notarize fields are involved.

## First run: setup wizard & packs

The packaged engine downloads its heavyweight runtime pieces as *packs*
(whisper model weights, the Windows CUDA runtime, the diarization worker)
through `GET/POST/DELETE /v1/packs`, with progress riding the same SSE
stream as job events. App-side:

- **Setup wizard** (`src/renderer/src/views/setup.ts`): auto-opens on first
  run (app-side flag in `app-config.json` under userData; set on completion
  or skip) when the engine is ready and recommended packs are missing. Shows
  detected hardware, pre-checks recommended packs with sizes, sets
  `whisper_device` from detected hardware (cuda iff Windows + NVIDIA with
  the CUDA pack available), installs with live progress, resumes interrupted
  downloads, and is re-runnable from Settings → "Run setup again".
- **Settings → Packs** (`src/renderer/src/views/packs-section.ts`): per-pack
  state/version/size/progress, install/uninstall (engine 409 reasons
  surfaced inline), re-download for `incompatible`/`failed` packs, license
  attributions from the engine-sent notices, and an advisory when
  `whisper_device=cuda` with no usable CUDA pack (uninstall never mutates
  the device setting).

E2e note: tests that launch with a fresh userData against an engine with
missing recommended packs must either pre-write
`{"first_run_complete": true}` to `<userData>/app-config.json` or assert the
wizard deliberately — otherwise the wizard replaces the Library view
(`tests/e2e/packs.spec.ts` covers the wizard flows).

**Unsigned-build caveats** (signing is user-blocking, tasks 6.4/6.5):

- Windows: SmartScreen shows "unrecognized app" — More info → Run anyway.
- macOS: Gatekeeper refuses double-click open; right-click → Open, or
  `xattr -d com.apple.quarantine "/Applications/Podcast Reader.app"`.
  macOS auto-update CANNOT be exercised unsigned (Squirrel.Mac refuses
  unsigned apps); the NSIS update path is testable unsigned.

## Auto-update

electron-updater against GitHub Releases with the FULL-DOWNLOAD strategy:
app and engine version in lockstep while the product is young, so
differential (blockmap) transfers would degrade to ~full size; the
extraResources layout keeps the differential path open as a config change
once shell and engine release cadences decouple (revisit trigger).

Flow: background download → consent prompt → the app-shell quit sequence
(engine fully terminated) → `quitAndInstall`. An update never replaces
files under a running engine. Updates are DISABLED in dev runs and on
unsigned builds (`updaterGate` in `src/main/updater.ts`; `BUILD_SIGNED`
flips in task 6.6). `PODCAST_READER_FORCE_UPDATES=1` re-enables them on a
packaged build for manually verifying the unsigned NSIS update path.

## Engine spawn resolution

When no live engine is adoptable, the app spawns one, resolving the command
in this order (design decision 2):

1. **Packaged engine** — `<resourcesPath>/engine/podcast-reader-engine serve`
   when it exists (installed builds; the payload comes from
   `packaging/build_engine.py` via `npm run dist -- --engine-dir`).
2. **`PODCAST_READER_ENGINE_CMD`** — an env override parsed by a **plain
   whitespace split** (per P6): no quoting, no escaping, so paths containing
   spaces are unsupported here — use posture 1 or 3 for those.
   Example: `PODCAST_READER_ENGINE_CMD="uv run podcast-reader serve"`.
   The value is the complete command, including `serve`.
3. **Dev fallback** — `uv run podcast-reader serve`, run from the repo root.

The spawned engine inherits the app's resolved `PODCAST_READER_DATA_DIR`
(per P9), so app and engine always agree on the data dir. Readiness is the
`PODCAST_READER_READY` stdout sentinel, then the discovery file
(`engine.json`) + token (`engine-state.json`) + authed `/v1/health` — no
port polling. An engine reporting a health version older than
`MIN_ENGINE_VERSION` (`src/main/version.ts`) is stopped and respawned; a
newer one is adopted (per P3/Q1).

## Engine respawn supervision

If a **spawned** engine exits unexpectedly mid-session, the app auto-respawns
it instead of stranding the user on a `failed` status. Detection keys off the
child-process exit event only, so it has no false-positive surface; **adopted**
engines (another instance already holds the per-install port) emit no exit
event and keep the prior behavior.

The bounded policy (`src/main/respawn-policy.ts`, pure + unit-tested) backs off
between attempts — **1s / 2s / 4s** — and gives up to a terminal `failed`
status after **three** consecutive failed attempts; the failure budget resets
once the engine has run healthy for **60s**. Each respawn reconstructs the same
live state as a cold start: it re-pushes the vaulted keys (still before
broadcasting `ready`), aborts the dead engine's SSE stream before opening a
fresh one (the port and token are stable across respawns, so the old stream
would otherwise re-attach to the new engine), and re-arms exit detection.
In-flight jobs survive via the engine's persistent journal plus the app's
reconnect-then-rehydrate.

While respawning, the app reports a `restarting` engine status (the renderer
shows "Reconnecting to engine… (attempt N/3)"); during that window the engine
is reported not-ready to the `app://media` path, so it never proxies to a dead
engine. From the terminal `failed` state a **Restart engine** button calls
`window.api.engineRestart()`, which resets the budget and spawns a fresh engine
without going through the quit sequence (concurrent invocations are a no-op).
The respawn loop is quit-safe: `quit()` sets a quitting flag first, and the
loop re-checks it after the backoff and again after the spawn — a child spawned
after a quit began is SIGKILL'd rather than wired up.

## Floating media player

The Reader hosts a draggable, resizable floating player synced to the
transcript. It asks the engine `GET /v1/media/{source_id}/info` for the player
kind and plays accordingly:

- **YouTube** → an embedded `youtube-nocookie` iframe driven by the *raw*
  YouTube iframe `postMessage` protocol. The YouTube JS API is deliberately
  **not** loaded — third-party script must never run in the `window.api`
  context — so the only CSP allowance is `frame-src`, no `script-src`.
- **Local / remote** → the `<video>`/`<audio>` element loads
  `app://media/<source_id>`, an internal privileged scheme handled in the main
  process (`media-protocol.ts`). The handler validates the sha256 `source_id`,
  adds the engine bearer token (which the renderer never holds), forwards the
  `Range` header, and returns the engine response verbatim — so seeking works
  and the renderer stays credential-free. Remote sources are lazily downloaded
  by the engine into a bounded LRU cache on first play.

Sync is bidirectional over `postMessage` (channel `pr-sync`): click a
transcript passage to seek; the current passage highlights and scrolls as
media plays. Because the transcript runs in an opaque-origin sandboxed iframe
(and the YouTube iframe posts its own control messages), every sync message is
validated by **both** the `pr-sync` channel tag and `event.source` identity.
The `app://` scheme is registered privileged (standard/secure/stream) at
module load, before `app.whenReady`.

## Layout

| Path | Purpose |
|------|---------|
| `src/main/` | Main process: engine supervision, engine client/SSE, key vault, IPC, deep-link + `app://media` protocol handlers, auto-update |
| `src/preload/` | contextBridge API (`window.api`) — the renderer's only door |
| `src/renderer/` | Renderer (vanilla TS): Library / Reader / New / Settings views, hash router; Reader's floating `media-player` + `sync-bridge` |
| `src/shared/` | Types mirrored from `podcast_reader/types.py` + the IPC contract |
| `tests/mock-engine/` | Scriptable `/v1` mock server (separate process; honors the real handshake) |
| `tests/e2e/` | Playwright specs: mock-engine e2e + the real-engine integration smoke |
| `scripts/dist.mjs` | Installer build wrapper (`--engine-dir` input → extraResources) |
| `scripts/build-icons.mjs` | Dev step: render `build/icon.svg` → `build/icon.png` (1024) via rsvg-convert; PNG magic + dimension asserts |
| `build/icon.svg` + `build/icon.png` | Committed icon source + render; electron-builder derives `.icns`/`.ico` |
| `electron-builder.config.cjs` | Packaging config: icon, NSIS/dmg+zip targets, protocol registration, publish |

Linux note: this app ships for Windows + macOS; on headless/dev Linux,
`safeStorage` may be unavailable, in which case API keys are held in memory
for the session only (a Settings warning surfaces this).
