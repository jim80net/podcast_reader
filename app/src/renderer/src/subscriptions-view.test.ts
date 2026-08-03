import { describe, expect, it } from 'vitest'

import {
  emailPreferenceControls,
  emailPreferenceStatus,
  subscriptionControls,
  subscriptionStatus
} from './views/subscriptions'
import type { PremiumProductState } from '../../shared/ipc'

describe('Subscriptions state matrix', () => {
  it('keeps mutations off for Local, free, unavailable, and flag-disabled premium', () => {
    const states: PremiumProductState[] = [
      { state: 'local', available: true },
      { state: 'online-free', available: true, expiresAt: 1 },
      { state: 'online-unavailable', available: true },
      { state: 'online-premium', available: true, expiresAt: 1, subscriptionsAvailable: false, emailAvailable: false }
    ]
    for (const state of states) expect(subscriptionControls(state).mutations).toBe(false)
    expect(subscriptionControls(states[0] as PremiumProductState).connect).toBe(true)
    expect(subscriptionStatus(states[1] as PremiumProductState)).toContain('will not be sent')
    expect(subscriptionStatus(states[2] as PremiumProductState)).toContain('local list is retained')
  })

  it('enables mutations only for current premium with the capability flag', () => {
    const state: PremiumProductState = {
      state: 'online-premium', available: true, expiresAt: 1, subscriptionsAvailable: true, emailAvailable: true
    }
    expect(subscriptionControls(state)).toEqual({ mutations: true, connect: false })
    expect(subscriptionStatus(state)).toContain('active')
  })

  it('keeps email enable premium-only while preserving revocation in every state', () => {
    const local: PremiumProductState = { state: 'local', available: true }
    const free: PremiumProductState = { state: 'online-free', available: true, expiresAt: 1 }
    const unavailable: PremiumProductState = { state: 'online-unavailable', available: true }
    const flagOff: PremiumProductState = {
      state: 'online-premium', available: true, expiresAt: 1,
      subscriptionsAvailable: true, emailAvailable: false
    }
    const enabled: PremiumProductState = { ...flagOff, emailAvailable: true }
    for (const state of [local, free, unavailable, flagOff]) {
      expect(emailPreferenceControls(state, false).mutation).toBe(false)
      expect(emailPreferenceControls(state, true).mutation).toBe(true)
    }
    expect(emailPreferenceControls(enabled, false).mutation).toBe(true)
    expect(emailPreferenceStatus(unavailable, true)).toContain('still turn this preference off')
    expect(emailPreferenceStatus(enabled, false)).toContain('Off')
  })
})
