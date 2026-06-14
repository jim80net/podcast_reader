# Engine Respawn Supervision — Design

**Date:** 2026-06-14
**Status:** Approved design v2, post-systems-review (pre-openspec)
**Author:** Jim Park, with Claude
**Review history:** v1 — approved in brainstorm (scope: spawned-child-exit detection
only; backoff + give-up-after-3 + healthy-uptime reset; manual retry; quit-safe).
v2 — systems-review findings: crash path nulls handle/client/stream immediately so
`media` reports not-ready during restart and ownership is unambiguous (C1/C2/M5); the
`quitting` flag is set first in `quit()` and re-checked by the respawn loop after every
await, SIGKILL-ing a just-spawned child if quitting won the race (C1/C2); renderer
`renderEngineStatus` gains a `restarting` arm AND an `assertNever` default (H1); give-up
boundary and `lastReadyAt` init pinned (M1); respawn status owned solely by
`observeChildExit`/policy, `onConnectionChange` stays unsubscribed (M2); `restart()` is
re-entrancy-guarded and bypasses `quit()` (L1); the e2e death seam is a real
`process.exit` (L2). The linchpin holds: `ensure()`/`tryAdopt` probes the discovery PID
and spawns fresh when it is dead (`engine.ts:142`).

## Problem

If the engine process dies mid-session, the desktop app surfaces a `failed` status
and stops there — the user must restart the whole app to recover. `engine-manager.ts`
already detects this (`observeChildExit`, line 163) and its comment marks the gap:
*"Respawn supervision is a follow-up — for now the user restarts the app."* This change
closes that gap with a bounded, quit-safe auto-respawn.

## Goals

- Automatically respawn a **spawned** engine that exits unexpectedly, restoring a
  working session without an app restart.
- Bound the behavior: backoff between attempts, give up after repeated failures, reset
  after the engine has run healthy.
- Preserve every existing invariant: the quit sequence is never fought, vaulted keys are
  re-pushed, in-flight jobs survive via the engine journal + the app's reconnect-rehydrate.
- A buggy supervisor is worse than no supervisor — favor zero false positives.

## Non-goals (out of scope)

- **Health/SSE-based liveness detection** for adopted engines (no exit event) or a
  hung-but-alive engine. A clean extension point is left; v1 does not build it (it would
  have to debounce against the SSE consumer's own reconnect/backoff and risks killing a
  healthy engine on a transient blip).
- Crash telemetry / reporting.

## Scope decision: detection

Respawn triggers **only** on a spawned engine's `childExited` (the existing seam). The app
spawns the engine on essentially every launch; an *adopted* engine (another instance
already holds the fixed per-install port) is the rare edge case and keeps today's
behavior. A process-exit event is unambiguous, so detection has no false-positive surface.

## Architecture

All supervision stays in `EngineManager` (`app/src/main/engine-manager.ts`), the existing
composition root. The respawn policy is factored into a small, pure, independently-testable
unit so the manager's wiring stays thin.

```
spawned engine exits  ──observeChildExit──▶  is this still our handle, not quitting?
                                              │ yes
                                              ▼
                                   RespawnPolicy.next(now)
                              ┌───────────────┴───────────────┐
                        give up (≥3 fails)              attempt N (delay d)
                              │                                │
                       status: failed                   status: restarting
                       (+ manual Retry)                       │ sleep d
                                                              ▼
                                                  ensure() → re-wire → ready
                                                  (re-push keys, re-stream)
                                                              │
                                              healthy ≥60s → policy.reset()
```

## Components

### `RespawnPolicy` (new, pure) — `app/src/main/respawn-policy.ts`
A small state machine, no I/O, injected clock:
- `markReady(now)` — stamps the last time the engine reached `ready` (no timer started).
  `lastReadyAt` initializes to `0`, so a crash before the engine ever reached `ready`
  satisfies the lazy reset and simply resets an already-0 count (harmless).
- `recordFailure(now)` → `{ action: 'retry'; delayMs } | { action: 'give-up' }`. First, if
  `now - lastReadyAt ≥ HEALTHY_RESET_MS` (60s), the consecutive-failure count is reset to 0
  (a crash after a long healthy run starts a fresh burst — no background timer needed, the
  check is lazy at failure time). Then it increments the count. **Boundary (pinned):**
  count 1 → `retry` 1s, count 2 → `retry` 2s, count 3 → `retry` 4s, count 4 → `give-up`
  — i.e. three respawn attempts (using all three backoff delays), then give up on the
  fourth consecutive crash. Jitter is added via an injected source so it stays deterministic.
- `reset()` — clears the count (used by manual `restart()`).
- Pure and deterministic: unit-tested in isolation, with an explicit boundary table
  (failures 1–4 → retry@1s / retry@2s / retry@4s / give-up; reset after a ≥60s-healthy run).

### `EngineManager` (extended)
- Refactor the post-spawn wiring in `start()` (observeChildExit, createClient, push keys,
  SSE stream, ready status) into a private `wireUp(handle)` reused by both `start()` and
  respawn — so a respawn reconstructs exactly the same live state. **`wireUp` aborts any
  existing `this.stream` before creating the new one:** the per-install port and token are
  stable across respawns, so the crashed engine's old SSE stream (which reconnects with
  backoff) would otherwise silently re-attach to the *new* engine, producing a duplicate
  event stream. Aborting first guarantees exactly one stream.
- **Crash path nulls ownership immediately (M5/C2).** `observeChildExit` calls a new
  `private handleUnexpectedExit(handle)` which — after the `this.handle !== handle` and
  `!quitting` guards — sets `this.handle = null`, `this.engineClient = null`, and aborts
  `this.stream`. This makes the `media` getter (and any "is the engine live" check) report
  not-ready during the restart window (preserving its "answers 503 outside the engine's
  live window" invariant), and removes the dead handle that would otherwise confuse `quit()`.
- `handleUnexpectedExit` then consults `RespawnPolicy.recordFailure(now())`: on `give-up`
  → status `failed` (message: engine keeps crashing); on `retry` → status `restarting`,
  `await sleep(delayMs)`, then run the respawn (below).
- **`markReady` is stamped inside `wireUp`** at the same verified-ready point `start()`
  broadcasts `ready` (after the key-push loop), so every (re)spawn that reaches ready resets
  the healthy clock — not just the first start.
- **Quit-safety with explicit checkpoints (C1/C2).** `quit()` sets `this.quitting = true`
  **first**, before reading `this.handle`. The respawn routine re-checks `this.quitting`
  **after the backoff sleep** and **again after `ensure()` resolves**; if quitting won the
  race after `ensure()`, it SIGKILLs the just-spawned child (via the existing force-kill
  path) instead of wiring it up, and sets `stopped`. So no engine is ever spawned after a
  quit, and no corpse is wired up. `observeChildExit` also keeps its `this.handle !== handle`
  guard (a graceful-shutdown exit during `quit()` is ignored).
- A failed `ensure()` during respawn counts as a failure → back into `RespawnPolicy` (a
  respawn that can't even spawn still backs off and eventually gives up).
- **`restart()` (manual recovery from `failed`, IPC-exposed):** re-entrancy-guarded (a
  no-op if a start/respawn is already in flight); it does **not** go through `quit()` (there
  is no live engine to shut down — the crash path already nulled the handle). It clears
  `quitting`, calls `RespawnPolicy.reset()`, aborts any lingering stream, then runs
  `ensure()` + `wireUp()`.
- **Respawn status is owned solely by `observeChildExit`/`RespawnPolicy` (M2).** The manager
  does not subscribe to the SSE stream's `onConnectionChange`, so a crashing engine's stream
  drop never races the `restarting`/`ready` transitions. Keep it unsubscribed.

### IPC + renderer
- `EngineStatus` (`app/src/shared/ipc.ts`) gains `{ state: 'restarting'; attempt; maxAttempts }`.
- A new `engine:restart` invoke channel → `manager.restart()`, exposed on `window.api`.
- The renderer's engine-status surface shows a brief "Reconnecting to engine… (attempt
  N/3)" banner for `restarting`, and a "Restart engine" button on `failed`.
- **Exhaustiveness (H1).** `renderEngineStatus` (`app/src/renderer/src/main.ts`) currently
  has no `default`/exhaustiveness check, so a new union member silently renders nothing.
  Add the `case 'restarting'` arm **and** an `assertNever(status)` default so future
  `EngineStatus` additions fail the build. Other consumers test `=== 'ready'` and need no
  change (a `restarting` value is correctly not-ready).

## Data flow / job survival

Respawn does not touch jobs. The engine's persistent journal recovers interrupted jobs on
restart, and the app's SSE consumer re-hydrates from `GET /v1/jobs` after the stream
reconnects (existing behavior). The renderer's job store updates from that hydration.

## Error handling

- `ensure()` failing during respawn counts as a failure → back into `RespawnPolicy`
  (so a respawn that can't even spawn still backs off and eventually gives up).
- Re-pushing keys on respawn follows the existing push-at-start path (failures ride on the
  ready status, exactly as in `start()`).

## Testing

- **`respawn-policy.test.ts`** (pure): the pinned boundary table (failures 1–4 →
  retry@1s / retry@2s / retry@4s / give-up), the lazy healthy reset (≥60s since
  `markReady` resets the count; <60s does not), and `reset()`, all with an injected
  clock + jitter source.
- **`engine-manager` unit tests** (mocked deps): unexpected exit → `restarting` then
  `ready`, with the old stream aborted, keys re-pushed, and a new stream created;
  `markReady` resets the policy after a healthy run; **exit during quit → no respawn**
  and **quit during backoff / after ensure() → the just-spawned child is SIGKILL'd, no
  engine survives, status `stopped`** (the C1/C2 checkpoints); give-up after the 4th crash
  → `failed`; `media` getter returns null while `restarting`; `restart()` resets and
  respawns and is a no-op while a respawn is in flight.
- **Playwright e2e**: the mock engine **actually exits its process** (a real
  `process.exit` death seam, not just an SSE close — the respawn trigger is `childExited`,
  a process-exit event); assert the app shows `restarting` then returns to `ready` and the
  library still loads.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Crash-loop hammering CPU | exponential backoff + give-up-after-3 |
| Respawn fights the quit sequence | `quitting` set first in `quit()` + re-checked after each respawn await (SIGKILL a child spawned after quit) + `this.handle !== handle` guard |
| A long-healthy engine's first crash is treated as a burst | reset the count after ≥60s healthy (`markReady` in `wireUp`) |
| Respawn leaves stale keys/stream | reuse the single `wireUp` path start() uses; `wireUp` aborts the old stream first (else it re-attaches to the new engine on the stable port+token) |
| `media` getter points at a dead engine during restart | crash path nulls `handle`/`engineClient` immediately → getter returns null → `app://media` gives a clean 503, not a hang |
| New `restarting` union member silently unhandled in the UI | `renderEngineStatus` gains the arm **and** an `assertNever` default |
| False positives | none — detection is an unambiguous process-exit event only |

## Follow-ons (tracked, not built here)

1. Health/SSE-based liveness detection (adopted + hung engines).
2. Crash telemetry to inform whether a given engine build is unstable.
