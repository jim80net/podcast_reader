import { describe, expect, it } from 'vitest'

import { registerIpcHandlers } from './ipc'
import { CHANNELS } from '../shared/ipc'
import type { EngineManager } from './engine-manager'
import type { PremiumAccess } from './premium/controller'

describe('subscription IPC authorization', () => {
  it('does not forward a free-tier feed URL to the engine', async () => {
    const handlers = new Map<string, (...args: unknown[]) => unknown>()
    const calls: unknown[][] = []
    const manager = {
      client: new Proxy({}, { get: (_target, property: string) => (...args: unknown[]) => { calls.push([property, ...args]); return Promise.resolve([]) } }),
      status: { state: 'ready' }, port: 1, keyStorageMode: 'encrypted'
    } as unknown as EngineManager
    const premium = access(false)
    registerIpcHandlers(
      { handle: (channel, handler) => { handlers.set(channel, handler) } },
      manager,
      { status: () => ({ state: 'disabled', reason: 'test' }), installNow: async () => undefined },
      { isFirstRunComplete: () => true, markFirstRunComplete: () => undefined },
      undefined,
      premium
    )
    const create = handlers.get(CHANNELS.subscriptionsCreate)
    await expect(Promise.resolve().then(() => create?.({}, 'https://private.example/feed.xml'))).rejects.toThrow('premium_feature_unavailable')
    expect(calls).toEqual([])
  })
})

function access(enabled: boolean): PremiumAccess {
  return {
    state: () => ({ state: 'local', available: true }),
    restore: async () => ({ state: 'local', available: true }),
    connect: async () => ({ state: 'local', available: true }),
    signOut: () => ({ state: 'local', available: true }),
    background: () => ({ state: 'local', available: true }),
    subscriptionsEnabled: () => enabled,
    synchronizeCapability: async () => undefined,
    inventory: async () => null,
    openCta: async () => undefined
  }
}
