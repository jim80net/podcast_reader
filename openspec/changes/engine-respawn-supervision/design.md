# Engine Respawn Supervision — Design

Full design narrative, the respawn diagram, and the systems-review findings are in
`docs/superpowers/specs/2026-06-14-engine-respawn-supervision-design.md` (v2,
post-systems-review). This file captures the openspec-relevant decisions and the verified
constraints.

## Scope decision

Respawn triggers **only** on a spawned engine's `childExited` event (the existing
`observeChildExit` seam, `engine-manager.ts:163`). The app spawns the engine on essentially
every launch; an adopted engine (another instance already holds the fixed per-install port)
is the rare edge case and keeps today's behavior. A process-exit event is unambiguous → no
false-positive surface. Health/SSE-based liveness (adopted + hung engines) is a follow-on.

## Verified constraints (from the code, via systems-review)

- **Linchpin holds:** on respawn, `ensure()`/`tryAdopt` probes the discovery PID and, when
  it is dead (the crash case — a crash does not remove `engine.json`), returns null →
  `spawnEngine` spawns fresh (`engine.ts:142`). No hang, no corpse-adoption.
- **Double-stream is real:** the per-install port and bearer token are stable across
  respawns, and the SSE consumer reconnects with backoff until `abort()` — so the dead
  engine's old stream would re-attach to the new engine. `wireUp` aborts the old stream
  before creating the new one.
- **`childExited`:** spawned engines have it (so first AND subsequent crashes are caught);
  adopted engines have null (so they are out of scope, as intended).
- **Renderer exhaustiveness is NOT enforced:** `renderEngineStatus` (`main.ts`) has no
  default/`assertNever`, so a new union member silently renders nothing — the change adds
  both the `restarting` arm and the assertion. Other consumers test `=== 'ready'` (correct
  for a not-ready `restarting`).

## Key decisions

- **Ownership on crash:** `handleUnexpectedExit` nulls `handle`/`engineClient` and aborts
  the stream immediately, so `media` reports not-ready (clean 503) during the restart and
  `quit()` is never handed a corpse.
- **RespawnPolicy boundary (pinned):** failures 1→retry@1s, 2→retry@2s, 3→retry@4s,
  4→give-up; consecutive count resets lazily when `now - lastReadyAt ≥ 60s`
  (`markReady` stamped in `wireUp` at the verified-ready point); `reset()` for manual restart.
- **Quit-safety:** `quit()` sets `quitting` first; respawn re-checks after the backoff sleep
  AND after `ensure()` resolves, SIGKILL-ing a just-spawned child if quitting won — so no
  engine is spawned after quit and no corpse is wired up. `observeChildExit` keeps its
  `this.handle !== handle` guard.
- **Status ownership:** the `restarting`/`failed`/`ready` transitions during respawn are
  driven solely by `observeChildExit`/`RespawnPolicy`; the manager keeps `onConnectionChange`
  unsubscribed so a stream drop never races them.
- **Manual restart:** `restart()` is re-entrancy-guarded, clears `quitting`, resets the
  policy, aborts any lingering stream, and runs the spawn path — it does not call `quit()`
  (there is no live engine to shut down).

## Out of scope (follow-ons)

1. Health/SSE-based liveness detection (adopted + hung engines).
2. Crash telemetry.
