import { describe, expect, it, vi } from 'vitest'

import { registerIpcHandlers } from './ipc'
import { CHANNELS } from '../shared/ipc'
import type { EmailOutboxStatusWire } from './email-contracts'
import type { EngineManager } from './engine-manager'
import type { PremiumAccess } from './premium/controller'

const SUBSCRIPTION = 'sub_0123456789abcdef0123456789abcdef'
const SOURCE = 'a'.repeat(64)
const outbox: EmailOutboxStatusWire = {
  client_delivery_id: 'eml_AAAAAAAAAAAAAAAAAAAAAAAA',
  subscription_id: null,
  consent_kind: 'manual',
  state: 'pending',
  attempts: 0,
  error_code: null,
  created_at: '2026-08-03T04:00:00Z',
  updated_at: '2026-08-03T04:00:00Z',
  delivered_at: null
}

function access(emailEnabled: boolean, subject: string | null): PremiumAccess {
  return {
    state: () => ({ state: 'local', available: true }),
    restore: async () => ({ state: 'local', available: true }),
    connect: async () => ({ state: 'local', available: true }),
    signOut: () => ({ state: 'local', available: true }),
    background: () => ({ state: 'local', available: true }),
    subscriptionsEnabled: () => false,
    emailEnabled: () => emailEnabled,
    emailSubject: () => subject,
    wakeEmailSender: vi.fn(),
    emailUnavailable: () => undefined,
    synchronizeCapability: async () => undefined,
    inventory: async () => null,
    openCta: async () => undefined
  }
}

function harness(client: Record<string, (...args: unknown[]) => unknown>, premium: PremiumAccess) {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  const manager = {
    client,
    status: { state: 'ready' },
    port: 1,
    keyStorageMode: 'encrypted'
  } as unknown as EngineManager
  registerIpcHandlers(
    { handle: (channel, handler) => { handlers.set(channel, handler) } },
    manager,
    { status: () => ({ state: 'disabled', reason: 'test' }), installNow: async () => undefined },
    { isFirstRunComplete: () => true, markFirstRunComplete: () => undefined },
    undefined,
    premium
  )
  const invoke = (channel: string, ...args: unknown[]): Promise<unknown> =>
    Promise.resolve().then(() => handlers.get(channel)?.({}, ...args))
  return { handlers, invoke }
}

describe('email IPC authorization and minimization', () => {
  it('never forwards an enable intent without fresh transcript-email capability', async () => {
    const setEmailPreference = vi.fn()
    const h = harness({ setEmailPreference }, access(false, 'usr_free'))
    await expect(h.invoke(CHANNELS.emailPreferenceSet, SUBSCRIPTION, true)).rejects.toThrow(
      'premium_feature_unavailable'
    )
    expect(setEmailPreference).not.toHaveBeenCalled()
  })

  it('derives the subject in main and preserves local revocation while unavailable', async () => {
    const setEmailPreference = vi.fn(async () => ({
      subscription_id: SUBSCRIPTION,
      enabled: false,
      consent_revision: 2
    }))
    const h = harness({ setEmailPreference }, access(false, 'usr_last_known'))
    await expect(h.invoke(CHANNELS.emailPreferenceSet, SUBSCRIPTION, false)).resolves.toEqual({
      subscriptionId: SUBSCRIPTION,
      enabled: false,
      consentRevision: 2
    })
    expect(setEmailPreference).toHaveBeenCalledWith(SUBSCRIPTION, 'usr_last_known', false)
  })

  it('coalesces a double-click and creates the action identifier only in main', async () => {
    let finish!: (value: EmailOutboxStatusWire) => void
    const pending = new Promise<EmailOutboxStatusWire>((resolve) => { finish = resolve })
    const createManualEmail = vi.fn((_actionId: unknown, _sourceId: unknown) => pending)
    const premium = access(true, 'usr_premium')
    const h = harness({ createManualEmail }, premium)
    const first = h.invoke(CHANNELS.emailManualCreate, SOURCE)
    const second = h.invoke(CHANNELS.emailManualCreate, SOURCE)
    await vi.waitFor(() => expect(createManualEmail).toHaveBeenCalledOnce())
    finish(outbox)
    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.objectContaining({ clientDeliveryId: outbox.client_delivery_id }),
      expect.objectContaining({ clientDeliveryId: outbox.client_delivery_id })
    ])
    const [actionId, sourceId] = createManualEmail.mock.calls[0] ?? []
    expect(actionId).toMatch(/^act_[A-Za-z0-9_-]{24}$/)
    expect(sourceId).toBe(SOURCE)
    expect(premium.wakeEmailSender).toHaveBeenCalledOnce()
  })
})
