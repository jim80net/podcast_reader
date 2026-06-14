import { describe, expect, it } from 'vitest'

import { HEALTHY_RESET_MS, MAX_RESPAWN_ATTEMPTS, RespawnPolicy } from './respawn-policy'

/**
 * The respawn policy is pure and deterministic: an injected clock (passed to
 * each call) and an injected jitter source (constructor) make every assertion
 * exact. The boundary table is PINNED by the design — three retries using all
 * three backoff delays (1s/2s/4s), then give up on the fourth consecutive
 * crash — so these tests guard that contract.
 */

/** A policy with jitter disabled, so delayMs is exactly the base backoff. */
function noJitterPolicy(): RespawnPolicy {
  return new RespawnPolicy({ jitter: () => 0 })
}

describe('RespawnPolicy.recordFailure — pinned boundary table', () => {
  it('retries 1s/2s/4s for the first three crashes, then gives up on the fourth', () => {
    const policy = noJitterPolicy()
    expect(policy.recordFailure(0)).toEqual({ action: 'retry', delayMs: 1000 })
    expect(policy.recordFailure(0)).toEqual({ action: 'retry', delayMs: 2000 })
    expect(policy.recordFailure(0)).toEqual({ action: 'retry', delayMs: 4000 })
    expect(policy.recordFailure(0)).toEqual({ action: 'give-up' })
  })

  it('exposes the attempt budget that matches the boundary (MAX_RESPAWN_ATTEMPTS = 3)', () => {
    expect(MAX_RESPAWN_ATTEMPTS).toBe(3)
  })
})

describe('RespawnPolicy jitter', () => {
  it('adds the injected jitter to the base backoff', () => {
    const policy = new RespawnPolicy({ jitter: () => 250 })
    expect(policy.recordFailure(0)).toEqual({ action: 'retry', delayMs: 1250 })
    expect(policy.recordFailure(0)).toEqual({ action: 'retry', delayMs: 2250 })
  })
})

describe('RespawnPolicy healthy reset (lazy, at failure time)', () => {
  it('resets the count when ≥60s have elapsed since the last markReady', () => {
    const policy = noJitterPolicy()
    policy.recordFailure(0) // count 1 → 1s
    policy.recordFailure(0) // count 2 → 2s
    policy.markReady(10_000) // engine reached ready at t=10s

    // A crash 60s after ready resets the burst → back to count 1 → 1s.
    expect(policy.recordFailure(10_000 + HEALTHY_RESET_MS)).toEqual({
      action: 'retry',
      delayMs: 1000
    })
  })

  it('does NOT reset when <60s have elapsed since the last markReady', () => {
    const policy = noJitterPolicy()
    policy.recordFailure(0) // count 1
    policy.recordFailure(0) // count 2
    policy.markReady(10_000)

    // A crash 59.999s after ready stays in the burst → count 3 → 4s.
    expect(policy.recordFailure(10_000 + HEALTHY_RESET_MS - 1)).toEqual({
      action: 'retry',
      delayMs: 4000
    })
  })

  it('resets a never-ready engine harmlessly (lastReadyAt init 0)', () => {
    const policy = noJitterPolicy()
    // The first crash, before markReady was ever called: now (=70s) - 0 ≥ 60s,
    // so the (already-0) count is reset, then incremented to 1 → 1s.
    expect(policy.recordFailure(70_000)).toEqual({ action: 'retry', delayMs: 1000 })
  })
})

describe('RespawnPolicy.reset', () => {
  it('clears the consecutive-failure count (manual restart)', () => {
    const policy = noJitterPolicy()
    policy.recordFailure(0)
    policy.recordFailure(0)
    policy.recordFailure(0)
    policy.reset()
    // Fresh budget after reset → back to the first retry.
    expect(policy.recordFailure(0)).toEqual({ action: 'retry', delayMs: 1000 })
  })
})
