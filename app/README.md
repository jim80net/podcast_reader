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
npm run typecheck  # tsc --noEmit (node + web projects)
npm run lint       # eslint
npm run build      # electron-vite production build into out/
```

## Engine spawn resolution (pre-Phase 4 dev posture)

When no live engine is adoptable, the app spawns one, resolving the command
in this order (design decision 2):

1. **Packaged engine** — `<resourcesPath>/engine/podcast-reader-engine serve`
   when it exists (installed builds; the payload lands in Phase 4).
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

## Layout

| Path | Purpose |
|------|---------|
| `src/main/` | Main process: engine supervision, engine client/SSE, key vault, IPC, protocol handler |
| `src/preload/` | contextBridge API (`window.api`) — the renderer's only door |
| `src/renderer/` | Renderer (vanilla TS; minimal shell until the group-4 views land) |
| `src/shared/` | Types mirrored from `podcast_reader/types.py` + the IPC contract |

Linux note: this app ships for Windows + macOS; on headless/dev Linux,
`safeStorage` may be unavailable, in which case API keys are held in memory
for the session only (a Settings warning surfaces this).
