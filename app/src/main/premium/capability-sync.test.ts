import { describe, expect, it, vi } from 'vitest'

import { PremiumController } from './controller'
import type { ProductState } from './contracts'
import type { PremiumRuntime } from './runtime'
import type { PremiumDeviceFlow } from './device-flow'
import type { PremiumTransport } from './transport'

const premium: ProductState = {
  state: 'online-premium',
  subject: 'usr_premium',
  refreshAfter: Date.parse('2026-08-03T00:05:00Z'),
  entitlementRevision: 7,
  flagsRevision: 12,
  adPolicy: 'none',
  podcastSubscriptions: true,
  transcriptEmail: true
}

describe('PremiumController capability handoff', () => {
  it('disables first, then sends the exact memory snapshot, and clears on sign-out', async () => {
    let state: ProductState = { state: 'local' }
    const snapshots: unknown[] = []
    const emailSnapshots: unknown[] = []
    const sender = { enable: vi.fn(), disable: vi.fn(), wake: vi.fn() }
    const runtime = {
      get state() { return state },
      get bearer() { return null },
      restore: async () => { state = premium; return state },
      acceptTokens: async () => { state = premium; return state },
      signOut: () => { state = { state: 'local' }; return state },
      background: () => { state = { state: 'online-unavailable' }; return state }
    }
    const controller = new PremiumController({
      runtime: runtime as unknown as PremiumRuntime,
      transport: {} as PremiumTransport,
      deviceFlow: {} as PremiumDeviceFlow,
      openExternal: async () => undefined,
      invalidated: vi.fn(),
      stateChanged: vi.fn(),
      now: () => Date.parse('2026-08-03T00:00:00Z'),
      schedule: () => 1 as unknown as ReturnType<typeof setTimeout>,
      cancel: vi.fn(),
      syncCapability: async (snapshot) => { snapshots.push(snapshot) },
      syncEmailCapability: async (snapshot) => { emailSnapshots.push(snapshot) },
      emailSender: sender,
      capabilitySyncFailed: vi.fn()
    })

    await controller.restore()
    await controller.synchronizeCapability()
    expect(snapshots.slice(0, 2)).toEqual([
      { schema_version: 1, subject: 'usr_premium', entitlement_revision: 7, flags_revision: 12, podcast_subscriptions: false, expires_at: '2026-08-03T00:05:00Z' },
      { schema_version: 1, subject: 'usr_premium', entitlement_revision: 7, flags_revision: 12, podcast_subscriptions: true, expires_at: '2026-08-03T00:05:00Z' }
    ])
    expect(emailSnapshots.slice(0, 2)).toEqual([
      { schema_version: 1, subject: 'usr_premium', entitlement_revision: 7, flags_revision: 12, transcript_email: false, expires_at: '2026-08-03T00:05:00Z' },
      { schema_version: 1, subject: 'usr_premium', entitlement_revision: 7, flags_revision: 12, transcript_email: true, expires_at: '2026-08-03T00:05:00Z' }
    ])
    expect(sender.enable).toHaveBeenCalledWith('usr_premium')
    controller.signOut()
    await controller.synchronizeCapability()
    expect(snapshots.at(-1)).toMatchObject({ podcast_subscriptions: false })
    expect(emailSnapshots.at(-1)).toMatchObject({ transcript_email: false })
    expect(sender.disable).toHaveBeenCalled()
    expect(controller.subscriptionsEnabled()).toBe(false)
    expect(controller.emailEnabled()).toBe(false)
  })

  it('never sends an enabling snapshot when the preceding disable fails', async () => {
    const calls: boolean[] = []
    const failed = vi.fn()
    const runtime = {
      get state() { return premium },
      get bearer() { return null },
      restore: async () => premium,
      acceptTokens: async () => premium,
      signOut: () => ({ state: 'local' } as ProductState),
      background: () => ({ state: 'online-unavailable' } as ProductState)
    }
    const controller = new PremiumController({
      runtime: runtime as unknown as PremiumRuntime,
      transport: {} as PremiumTransport,
      deviceFlow: {} as PremiumDeviceFlow,
      openExternal: async () => undefined,
      invalidated: vi.fn(),
      stateChanged: vi.fn(),
      now: () => Date.parse('2026-08-03T00:00:00Z'),
      schedule: () => 1 as unknown as ReturnType<typeof setTimeout>,
      cancel: vi.fn(),
      syncCapability: async (snapshot) => {
        calls.push(snapshot.podcast_subscriptions)
        throw new Error('loopback unavailable')
      },
      capabilitySyncFailed: failed
    })
    await controller.synchronizeCapability()
    expect(calls).toEqual([false])
    expect(failed).toHaveBeenCalledOnce()
  })
})
