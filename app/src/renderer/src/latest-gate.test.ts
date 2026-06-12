import { describe, expect, it } from 'vitest'

import { LatestGate } from './latest-gate'

describe('LatestGate', () => {
  it('keeps a ticket current until a newer request starts', () => {
    const gate = new LatestGate()
    const first = gate.next()
    expect(first()).toBe(true)
    const second = gate.next()
    expect(first()).toBe(false)
    expect(second()).toBe(true)
  })

  it('only the latest of N overlapping requests may apply (out-of-order completion)', () => {
    const gate = new LatestGate()
    const tickets = [gate.next(), gate.next(), gate.next()]
    // Responses arrive in reverse order: only the last-issued ticket wins.
    expect(tickets[2]?.()).toBe(true)
    expect(tickets[1]?.()).toBe(false)
    expect(tickets[0]?.()).toBe(false)
  })
})
