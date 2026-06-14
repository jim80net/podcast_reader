# Engine Respawn Supervision

## Why

If the engine process dies mid-session, the desktop app surfaces a `failed` status and stops — the user must restart the whole app to recover. `engine-manager.ts` already detects this (`observeChildExit`) and its comment marks the gap: *"Respawn supervision is a follow-up — for now the user restarts the app."* This change closes it with a bounded, quit-safe auto-respawn so a crashed engine recovers without an app restart.

## What Changes

- **Auto-respawn** a spawned engine that exits unexpectedly: detect via the existing `observeChildExit` seam (a real process-exit event — no false-positive surface), then respawn with backoff, restoring a working session.
- **Bounded policy** in a new pure `RespawnPolicy` (`app/src/main/respawn-policy.ts`): backoff 1s/2s/4s across three attempts, then **give up** to a `failed` state on the fourth consecutive crash; the consecutive-failure count resets after the engine has run healthy for ≥60s (lazy check, no background timer).
- **Quit-safe**: `quit()` sets a `quitting` flag first; the respawn routine re-checks it after every await and SIGKILLs a child spawned after a quit won the race, so no engine is ever spawned post-quit and no corpse is wired up.
- **Ownership on crash**: the crash path nulls `handle`/`engineClient` and aborts the old SSE stream immediately — so `media` reports not-ready during the restart window (clean 503, not a hang) and the old stream can't re-attach to the new engine on the stable port+token (no duplicate event stream).
- **Shared `wireUp`**: the post-spawn wiring (`observeChildExit`, client, key-push, ready status, SSE stream) is factored out and reused by both `start()` and respawn, so a respawn reconstructs exactly the same live state and re-pushes vaulted keys.
- **Status + manual recovery**: `EngineStatus` gains `{ state: 'restarting'; attempt; maxAttempts }`; the renderer shows a "Reconnecting…" banner and a "Restart engine" button on `failed` (new `engine:restart` IPC → re-entrancy-guarded `manager.restart()` that resets the policy and bypasses `quit()`). `renderEngineStatus` gains the `restarting` arm and an `assertNever` default so future status additions fail the build.

In-flight jobs need no special handling: the engine's persistent journal recovers interrupted jobs and the app re-hydrates from `GET /v1/jobs` after the SSE stream reconnects (existing behavior). Detection is **spawned-engine only**; health/SSE-based liveness for adopted/hung engines is a documented follow-on.

No breaking changes: the change is additive (a new status variant + IPC channel); existing supervision, spawn, and quit requirements are unchanged.

## Capabilities

### Modified Capabilities

- `app-shell`: adds engine respawn supervision (bounded auto-respawn of a crashed spawned engine, `restarting` status, manual restart) atop the existing supervision/spawn/quit requirements.

## Impact

- **Code:** new `app/src/main/respawn-policy.ts`; `engine-manager.ts` (`wireUp` extraction, `handleUnexpectedExit`, `quitting` flag + checkpoints, `markReady`, `restart()`); `app/src/shared/ipc.ts` (`restarting` status + `engine:restart` channel); `app/src/preload/index.ts` (`window.api` restart); `app/src/main/ipc.ts` (handler); `app/src/renderer/src/main.ts` (`restarting` arm + `assertNever`) and the status banner / restart button.
- **Tests:** `respawn-policy.test.ts` (boundary table, healthy reset); `engine-manager` unit tests (respawn re-wires + re-pushes keys; quit-during-backoff/after-ensure kills the child; give-up → failed; `media` null while restarting; `restart()` re-entrancy); Playwright e2e with a real `process.exit` death seam in the mock engine (restarting → ready).
- **Risk:** isolated to app supervision; no engine or extension change; favors zero false positives (process-exit detection only).
