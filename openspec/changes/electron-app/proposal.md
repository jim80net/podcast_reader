# Electron MVP (Desktop Phase 3)

## Why

Phases 1–2 produced a localhost engine (jobs, SSE, library, settings, in-memory keys) that nothing user-facing consumes yet — the desktop design's target user ("can install a desktop app; no terminal, no Python, no manual key files") still has no way in. Phase 3 builds the Electron app: the supervisor that spawns/adopts the engine, the four views that drive it, and the packaging/update machinery that turns the repo into an installable product.

## What Changes

- New `app/` workspace: Electron main process (engine supervision via the Phase 1 discovery handshake: spawn → ready sentinel → discovery file → health probe; adopt-or-kill of stale engines; explicit quit sequence), preload bridge, and a renderer with four views — Library, Reader (engine HTML artifact in a sandboxed surface), New (paste URL / drop file, live progress, awaiting-confirmation surfacing), Settings (provider dropdown, key entry + test button, whisper/model/storage settings).
- `safeStorage` key vault in the app: provider keys encrypted at rest on the app side, decrypted and pushed into engine memory via `PUT /v1/keys` at engine start and on change (the parent design's F9/K1 model). Keys never reach the renderer or the engine's disk.
- `podcast-reader://` protocol registration (installer + runtime): protocol-initiated jobs land in `awaiting-confirmation` and are confirmed by one click in the New view — never auto-executed.
- Engine additions the app needs (small, additive):
  - `POST /v1/jobs` gains `requires_confirmation`; new `POST /v1/jobs/{id}/confirm` and `DELETE /v1/jobs/{id}` (awaiting-confirmation only) make the Phase 1 reserved state reachable.
  - `POST /v1/shutdown` — portable graceful stop (Windows has no SIGTERM), so the app's quit sequence is: shutdown → engine reaps children → wait → exit/`quitAndInstall`.
  - `POST /v1/keys/test` — minimal provider round-trip behind the Settings "test key" button, reusing the one Phase 2 HTTP code path instead of duplicating the provider registry in TypeScript.
- electron-builder packaging: NSIS (Windows) + dmg/zip (macOS); the frozen engine dir ships as `extraResources` (executables cannot run from inside asar). Auto-update via electron-updater against GitHub Releases with **full-download updates** for this phase (rationale in design). Signing/notarization are explicit user-blocking prerequisite tasks — unsigned dev builds must work end-to-end first.
- Testing: Playwright e2e against a mock engine that honors the real discovery handshake; a real-engine spawn smoke test (dev posture: `uv run podcast-reader serve`); CI gains a node job. Tag-pipeline installer builds are deferred until signing credentials exist.
- Pre-Phase 4 dev posture, documented explicitly: no download manager yet, so first-run on dev machines assumes a Python env (`uv run podcast-reader serve`) or a locally built frozen engine dir; the packaged-engine payload contract is fixed here and filled by Phase 4.

No breaking changes: the engine API grows additively (existing clients' `POST /v1/jobs` behavior unchanged); CLI untouched.

## Capabilities

### New Capabilities

- `app-shell`: Electron main process — engine supervision (spawn/adopt-or-kill/handshake), quit sequence, key vault + push-at-start, protocol handler, renderer isolation and the main-process engine client.
- `app-views`: the four renderer views and their engine API consumption (library, reader artifact rendering, job submission + progress, settings/keys).
- `app-packaging`: electron-builder targets, engine payload layout, auto-update, signing/notarization gates, unsigned dev builds.

### Modified Capabilities

- `job-pipeline`: the reserved `awaiting-confirmation` state becomes reachable — confirmation-required submission, confirm and dismiss endpoints, restart recovery.
- `engine-service`: adds a graceful shutdown endpoint (`POST /v1/shutdown`) to the engine surface.
- `key-management`: adds `POST /v1/keys/test` (stacked on the pending `multi-provider-chapters` change, which archives before this one; delta is ADDED-only so it merges cleanly).

## Impact

- **Code:** new `app/` (TypeScript, electron-vite, no renderer framework — see design); engine edits in `engine/app.py` (three routes, `JobSubmission` field), `engine/jobs.py` (confirmation transitions), `engine/process.py` (shutdown wiring), `chapters.py`/`providers.py` reuse for key test.
- **Tests:** pytest additions for the new engine routes/transitions; new `app/` test stack — vitest unit, Playwright e2e (mock engine), real-engine smoke (integration-marked).
- **CI:** new node job (typecheck, unit, e2e under xvfb, real-engine smoke); installer builds deferred to tag pipelines once credentials exist.
- **Docs:** README (app section, dev posture), CLAUDE.md (app/ rows, new endpoints).
- **Deps:** `app/` brings electron, electron-vite, electron-builder, electron-updater, typescript, playwright, vitest (dev-side, npm); zero new Python runtime deps.
- **Out of scope:** download manager / first-run wizard (Phase 4), Chrome extension and pairing (Phase 5), hosted inference, Linux packaging.
