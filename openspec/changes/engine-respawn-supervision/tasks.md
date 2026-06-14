# Tasks — Engine Respawn Supervision

TDD: failing test first, then implementation. Run from `app/`: `npm run typecheck`,
`npm run lint`, `npm run test`; `npm run e2e` (xvfb-run -a on headless). Prefix shell
commands with `timeout`.

## 1. RespawnPolicy (pure, boundary-first)

- [ ] 1.1 `app/src/main/respawn-policy.ts`: `markReady(now)`, `recordFailure(now)` →
  `{action:'retry',delayMs}|{action:'give-up'}` (lazy reset when `now-lastReadyAt≥60s`;
  boundary 1→1s/2→2s/3→4s/4→give-up; injected jitter), `reset()`. `lastReadyAt` init 0.
- [ ] 1.2 `respawn-policy.test.ts`: the full boundary table, healthy reset (≥60s resets,
  <60s does not), `reset()` — injected clock + jitter, deterministic.

## 2. Status type + IPC

- [ ] 2.1 `app/src/shared/ipc.ts`: add `{ state: 'restarting'; attempt: number; maxAttempts: number }`
  to `EngineStatus`; add the `engine:restart` invoke channel to the IPC contract.
- [ ] 2.2 `app/src/preload/index.ts`: expose `window.api.engineRestart()` (typed); `ipc.ts`
  handler → `manager.restart()`. Update the isolation key-set assertion if `window.api` grew.

## 3. EngineManager

- [ ] 3.1 Extract the post-spawn block of `start()` into `private wireUp(handle)` (observe
  exit, create client, push keys, **markReady at the verified-ready point**, set `ready`,
  **abort old stream then** create+run the new stream). `start()` calls it. Unit test: a
  fresh start still wires up identically.
- [ ] 3.2 `handleUnexpectedExit(handle)`: guard `this.handle===handle && !quitting`; null
  `handle`/`engineClient`, abort stream; `RespawnPolicy.recordFailure` → `give-up`→`failed`,
  or `retry`→`restarting` + `sleep` + respawn. Unit test: crash → restarting → ready, keys
  re-pushed, one stream; `media` null while restarting.
- [ ] 3.3 Quit-safety: `quit()` sets `quitting=true` first; respawn re-checks `quitting`
  after the sleep AND after `ensure()` (SIGKILL the just-spawned child, set `stopped`).
  Unit tests: exit-during-quit → no respawn; quit-during-backoff and quit-after-ensure →
  no surviving engine.
- [ ] 3.4 `restart()`: re-entrancy guard; clear `quitting`; `RespawnPolicy.reset()`; abort
  lingering stream; `ensure()`+`wireUp()` (not via `quit()`). Unit tests: restart from
  `failed` respawns; double-invoke spawns one engine; give-up after the 4th crash → `failed`.

## 4. Renderer

- [ ] 4.1 `app/src/renderer/src/main.ts` `renderEngineStatus`: add the `restarting` arm
  (banner "Reconnecting to engine… (attempt N/maxAttempts)") and an `assertNever(status)`
  default; add a "Restart engine" button on `failed` → `window.api.engineRestart()`.
  Vitest for the status→render mapping incl. the assertNever guard.

## 5. e2e

- [ ] 5.1 Mock engine: a real `process.exit` death seam (e.g. `/__mock/crash`) so the
  spawned child genuinely exits and `childExited` resolves.
- [ ] 5.2 Playwright: trigger the crash; assert the app shows `restarting` then `ready` and
  the library still loads after respawn.

## 6. Docs

- [ ] 6.1 `app/README.md` (engine supervision section) + `CLAUDE.md` (engine-manager row):
  note auto-respawn (backoff/give-up/manual-restart, spawned-only) and the `restarting`
  status.
