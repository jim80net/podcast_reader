import { describe, expect, it } from 'vitest'

import { manualEmailAvailable, manualEmailStatus } from './views/reader'
import type { PremiumProductState } from '../../shared/ipc'

describe('Reader transcript email state matrix', () => {
  it('enables manual delivery only for fresh premium email capability', () => {
    const states: PremiumProductState[] = [
      { state: 'local', available: true },
      { state: 'online-free', available: true, expiresAt: 1 },
      { state: 'online-unavailable', available: true },
      {
        state: 'online-premium', available: true, expiresAt: 1,
        subscriptionsAvailable: true, emailAvailable: false
      }
    ]
    for (const state of states) expect(manualEmailAvailable(state)).toBe(false)
    expect(manualEmailStatus(states[0] as PremiumProductState)).toContain('Connect')
    expect(manualEmailStatus(states[1] as PremiumProductState)).toContain('requires premium')
    expect(manualEmailStatus(states[2] as PremiumProductState)).toContain('paused')
  })

  it('names the captured destination without exposing a recipient field', () => {
    const state: PremiumProductState = {
      state: 'online-premium', available: true, expiresAt: 1,
      subscriptionsAvailable: true, emailAvailable: true
    }
    expect(manualEmailAvailable(state)).toBe(true)
    expect(manualEmailStatus(state)).toBe('Email is delivered only to the Captured DEV mailbox.')
  })
})
