/**
 * The bounded respawn policy for engine supervision (engine-respawn-supervision
 * design): a small, pure, I/O-free state machine. The clock is injected per
 * call and the jitter source is injected at construction, so the manager's
 * wiring stays thin and the boundary is exactly unit-testable.
 *
 * The boundary is PINNED by the design: three respawn attempts using all three
 * backoff delays, then give up on the fourth consecutive crash. The healthy
 * reset is lazy (checked at failure time, no background timer): a crash ≥60s
 * after the engine last reached ready starts a fresh burst.
 */

/** The pinned per-attempt backoff (ms). Index 0 → first retry, etc. */
const BACKOFF_MS = [1000, 2000, 4000] as const

/** Three retries, then give up — i.e. give up on the fourth consecutive crash. */
export const MAX_RESPAWN_ATTEMPTS = BACKOFF_MS.length

/** A crash this long after the last `markReady` resets the failure budget. */
export const HEALTHY_RESET_MS = 60_000

export type RespawnDecision =
  | { action: 'retry'; delayMs: number }
  | { action: 'give-up' }

export interface RespawnPolicyOptions {
  /** Non-negative jitter (ms) added to each backoff. Default: deterministic 0. */
  jitter?: () => number
}

export class RespawnPolicy {
  private count = 0
  private lastReadyAt = 0
  private readonly jitter: () => number

  constructor(options: RespawnPolicyOptions = {}) {
    this.jitter = options.jitter ?? (() => 0)
  }

  /** Stamp the moment the engine reached ready (no timer started). */
  markReady(now: number): void {
    this.lastReadyAt = now
  }

  /** Clear the consecutive-failure count (manual restart). */
  reset(): void {
    this.count = 0
  }

  /**
   * Record an unexpected exit and decide what to do next. First, if the engine
   * ran healthy ≥60s since it last reached ready, the burst is reset (lazy, at
   * failure time). Then the count increments: attempts 1–3 retry with the
   * pinned backoff (+ jitter); the fourth gives up.
   */
  recordFailure(now: number): RespawnDecision {
    if (now - this.lastReadyAt >= HEALTHY_RESET_MS) this.count = 0
    this.count += 1
    const backoff = BACKOFF_MS[this.count - 1]
    if (backoff === undefined) return { action: 'give-up' }
    return { action: 'retry', delayMs: backoff + this.jitter() }
  }
}
