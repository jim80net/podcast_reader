# Engine Respawn Supervision — Design

**Date:** 2026-06-14
**Status:** Approved brainstorm, pre-systems-review
**Author:** Jim Park, with Claude
**Review history:** v1 — approved in brainstorm (scope: spawned-child-exit detection
only; backoff + give-up-after-3 + healthy-uptime reset; manual retry; quit-safe).

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
- `recordFailure(now)` → `{ action: 'retry'; delayMs } | { action: 'give-up' }`. First, if
  `now - lastReadyAt ≥ HEALTHY_RESET_MS` (60s), the consecutive-failure count is reset to 0
  (a crash after a long healthy run starts a fresh burst — no background timer needed, the
  check is lazy at failure time). Then it increments the count and returns `give-up` once it
  reaches `MAX_ATTEMPTS` (3), else the backoff delay for this attempt (1s, 2s, 4s; capped;
  small fixed jitter via an injected jitter source so it stays deterministic).
- `reset()` — clears the count (used by manual `restart()`).
- Pure and deterministic: unit-tested in isolation (schedule, give-up threshold, lazy reset).

### `EngineManager` (extended)
- Refactor the post-spawn wiring in `start()` (observeChildExit, createClient, push keys,
  SSE stream, ready status) into a private `wireUp(handle)` reused by both `start()` and
  respawn — so a respawn reconstructs exactly the same live state.
- `observeChildExit` calls a new `private handleUnexpectedExit()` instead of going straight
  to `failed`: consult `RespawnPolicy`; on `retry` set `restarting`, sleep the delay, then
  `ensure()` + `wireUp()`; on `give-up` set `failed` (message: engine keeps crashing).
- A `quitting` flag (set at the top of `quit()`) plus the existing `this.handle !== handle`
  guard makes respawn quit-safe under any interleaving.
- Mark a healthy engine (reset the policy) when a (re)spawn reaches `ready` and survives
  `HEALTHY_RESET_MS` — implemented by stamping the ready time and checking it on the next
  failure (no timer needed), which keeps the manager free of background timers.
- New `restart()` entry (IPC-exposed) for manual recovery from `failed`: resets the policy
  and runs the spawn path again.

### IPC + renderer
- `EngineStatus` (`app/src/shared/ipc.ts`) gains `{ state: 'restarting'; attempt; maxAttempts }`.
- A new `engine:restart` invoke channel → `manager.restart()`, exposed on `window.api`.
- The renderer's engine-status surface shows a brief "Reconnecting to engine… (attempt
  N/3)" banner for `restarting`, and a "Restart engine" button on `failed`.

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

- **`respawn-policy.test.ts`** (pure): backoff schedule, give-up after 3, counter reset
  after healthy uptime, with an injected clock + jitter.
- **`engine-manager` unit tests**: unexpected exit → `restarting` then `ready` (respawn
  re-pushes keys + re-establishes the stream via mocked deps); exit-during-quit → no
  respawn (quitting guard); give-up after N → `failed`; `restart()` resets and respawns.
- **Playwright e2e**: the mock engine exits (its `/v1/shutdown`-style death seam); assert
  the app shows `restarting` then returns to `ready` and the library still loads.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Crash-loop hammering CPU | exponential backoff + give-up-after-3 |
| Respawn fights the quit sequence | `quitting` flag + `this.handle !== handle` guard |
| A long-healthy engine's first crash is treated as a burst | reset the count after ≥60s healthy |
| Respawn leaves stale keys/stream | reuse the single `wireUp` path start() uses |
| False positives | none — detection is an unambiguous process-exit event only |

## Follow-ons (tracked, not built here)

1. Health/SSE-based liveness detection (adopted + hung engines).
2. Crash telemetry to inform whether a given engine build is unstable.
