import { describe, expect, it } from 'vitest'

import { subscriptionControls, subscriptionStatus } from './views/subscriptions'
import type { PremiumProductState } from '../../shared/ipc'

describe('Subscriptions state matrix', () => {
  it('keeps mutations off for Local, free, unavailable, and flag-disabled premium', () => {
    const states: PremiumProductState[] = [
      { state: 'local', available: true },
      { state: 'online-free', available: true, expiresAt: 1 },
      { state: 'online-unavailable', available: true },
      { state: 'online-premium', available: true, expiresAt: 1, subscriptionsAvailable: false }
    ]
    for (const state of states) expect(subscriptionControls(state).mutations).toBe(false)
    expect(subscriptionControls(states[0] as PremiumProductState).connect).toBe(true)
    expect(subscriptionStatus(states[1] as PremiumProductState)).toContain('will not be sent')
    expect(subscriptionStatus(states[2] as PremiumProductState)).toContain('local list is retained')
  })

  it('enables mutations only for current premium with the capability flag', () => {
    const state: PremiumProductState = {
      state: 'online-premium', available: true, expiresAt: 1, subscriptionsAvailable: true
    }
    expect(subscriptionControls(state)).toEqual({ mutations: true, connect: false })
    expect(subscriptionStatus(state)).toContain('active')
  })
})
