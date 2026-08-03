import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { PremiumController } from './controller'
import { PremiumRequestError } from './transport'
import type { ProductState } from './contracts'
import type { PremiumDeviceFlow } from './device-flow'
import type { PremiumRuntime } from './runtime'
import type { PremiumTransport } from './transport'

const NOW = Date.parse('2026-08-03T00:00:00Z')
const REFRESH_AFTER = Date.parse('2026-08-03T00:05:00Z')

const fixture = (name: string): unknown => JSON.parse(readFileSync(
  join(process.cwd(), '..', 'services', 'premium', 'contracts', 'v1', 'ads', name),
  'utf8'
)) as unknown

function harness(initial: ProductState, inventoryImpl: (slot: 'library' | 'reader', token: string) => Promise<unknown | null>) {
  let state = initial
  let bearer: string | null = initial.state === 'local' ? null : 'access_token_abcdefghijklmnopqrstuvwxyz'
  let restoreState = initial
  let now = NOW
  const timers: Array<() => void> = []
  const opened: string[] = []
  const invalidated = vi.fn()
  const stateChanged = vi.fn()
  const runtime = {
    get state() { return state },
    get bearer() { return bearer },
    restore: vi.fn(async () => { state = restoreState; bearer = state.state === 'local' ? null : 'refreshed_access_token_abcdefghijklmnop'; return state }),
    acceptTokens: vi.fn(async () => { state = restoreState; bearer = 'connected_access_token_abcdefghijklmnop'; return state }),
    signOut: vi.fn(() => { state = { state: 'local' }; bearer = null; return state }),
    background: vi.fn(() => { state = state.state === 'local' ? state : { state: 'online-unavailable' }; bearer = null; return state })
  }
  const transport = { inventory: vi.fn(inventoryImpl) }
  const deviceFlow = { authorize: vi.fn(async () => ({ access_token: 'a', refresh_token: 'r' })) }
  const controller = new PremiumController({
    runtime: runtime as unknown as PremiumRuntime,
    transport: transport as unknown as PremiumTransport,
    deviceFlow: deviceFlow as unknown as PremiumDeviceFlow,
    openExternal: async (url) => { opened.push(url) },
    invalidated,
    stateChanged,
    now: () => now,
    schedule: (callback) => { timers.push(callback); return timers.length as unknown as ReturnType<typeof setTimeout> },
    cancel: () => undefined
  })
  return {
    controller,
    runtime,
    transport,
    deviceFlow,
    invalidated,
    stateChanged,
    opened,
    timers,
    setRestoreState(value: ProductState) { restoreState = value },
    setNow(value: number) { now = value }
  }
}

const free = (adPolicy: 'none' | 'house' = 'house'): ProductState => ({
  state: 'online-free',
  subject: 'usr_free',
  refreshAfter: REFRESH_AFTER,
  entitlementRevision: 7,
  flagsRevision: 12,
  adPolicy,
  podcastSubscriptions: false,
  transcriptEmail: false
})

const premium: ProductState = {
  state: 'online-premium',
  subject: 'usr_premium',
  refreshAfter: REFRESH_AFTER,
  entitlementRevision: 7,
  flagsRevision: 12,
  adPolicy: 'none',
  podcastSubscriptions: true,
  transcriptEmail: true
}

describe('PremiumController house inventory boundary', () => {
  it('projects every online authorization variant without optional fallbacks', () => {
    expect(harness(free(), async () => null).controller.state()).toEqual({
      state: 'online-free',
      available: true,
      expiresAt: REFRESH_AFTER
    })
    expect(harness(premium, async () => null).controller.state()).toEqual({
      state: 'online-premium',
      available: true,
      expiresAt: REFRESH_AFTER,
      subscriptionsAvailable: true,
      emailAvailable: true
    })
  })

  it('makes zero inventory calls for Local, premium, unavailable, and free-without-house states', async () => {
    for (const state of [
      { state: 'local' } as ProductState,
      premium,
      { state: 'online-unavailable' } as ProductState,
      free('none')
    ]) {
      const h = harness(state, async () => fixture('eligible-library.json'))
      if (state.state === 'online-unavailable') h.setRestoreState({ state: 'online-unavailable' })
      await expect(h.controller.inventory('library')).resolves.toBeNull()
      expect(h.transport.inventory).not.toHaveBeenCalled()
    }
  })

  it('consumes the backend-owned fixture and exposes only bounded presentation fields', async () => {
    const h = harness(free(), async () => fixture('eligible-library.json'))
    await expect(h.controller.inventory('library')).resolves.toEqual({
      slot: 'library',
      expiresAt: REFRESH_AFTER,
      creative: {
        title: 'Read without losing your place',
        body: 'Podcast Reader turns long episodes into a private, searchable library.',
        ctaUrl: 'https://example.com/podcast-reader'
      }
    })
    expect(h.transport.inventory).toHaveBeenCalledWith('library', 'access_token_abcdefghijklmnopqrstuvwxyz')
  })

  it('keeps hostile strings inert as strings and rejects malformed or cross-slot inventory', async () => {
    const hostile = harness(free(), async () => fixture('hostile-text.json'))
    const result = await hostile.controller.inventory('library')
    expect(result?.creative.title).toBe("<script>alert('title')</script>")
    expect(result?.creative.body).toContain('<img src=x onerror=')

    for (const value of [fixture('malformed.json'), fixture('eligible-reader.json')]) {
      const rejected = harness(free(), async () => value)
      await expect(rejected.controller.inventory('library')).resolves.toBeNull()
      expect(rejected.runtime.background).toHaveBeenCalledOnce()
      expect(rejected.controller.state()).toEqual({ state: 'online-unavailable', available: true })
    }
  })

  it('accepts additive members without granting unknown consumed values', async () => {
    const value = fixture('eligible-library.json') as Record<string, unknown>
    const items = value.items as Array<Record<string, unknown>>
    value.future_top_level_member = true
    if (items[0] !== undefined) items[0].future_item_member = 'ignored'
    const h = harness(free(), async () => value)
    await expect(h.controller.inventory('library')).resolves.toMatchObject({ slot: 'library' })
  })

  it('opens only the exact fresh cached HTTPS CTA after an explicit request', async () => {
    const h = harness(free(), async () => fixture('eligible-library.json'))
    await h.controller.inventory('library')
    await expect(h.controller.openCta('library', 'https://example.com/podcast-reader')).resolves.toBeUndefined()
    expect(h.opened).toEqual(['https://example.com/podcast-reader'])
    await expect(h.controller.openCta('library', 'https://example.com/other')).rejects.toThrow('unavailable')
    await expect(h.controller.openCta('reader', 'https://example.com/podcast-reader')).rejects.toThrow('unavailable')
  })

  it('re-evaluates entitlement once on 401, retries with the rotated bearer, and stops after failure', async () => {
    let calls = 0
    const h = harness(free(), async () => {
      calls += 1
      if (calls === 1) throw new PremiumRequestError(401, 'invalid_token')
      return fixture('eligible-library.json')
    })
    h.setRestoreState(free())
    await expect(h.controller.inventory('library')).resolves.toMatchObject({ slot: 'library' })
    expect(h.runtime.restore).toHaveBeenCalledOnce()
    expect(h.transport.inventory).toHaveBeenNthCalledWith(2, 'library', 'refreshed_access_token_abcdefghijklmnop')

    const failed = harness(free(), async () => { throw new PremiumRequestError(401, 'invalid_token') })
    failed.setRestoreState({ state: 'local' })
    await expect(failed.controller.inventory('library')).resolves.toBeNull()
    expect(failed.transport.inventory).toHaveBeenCalledOnce()
  })

  it('serializes account refresh across simultaneous slot mounts', async () => {
    const h = harness({ state: 'online-unavailable' }, async (slot) => fixture(`eligible-${slot}.json`))
    h.setRestoreState(free())
    await expect(Promise.all([
      h.controller.inventory('library'),
      h.controller.inventory('reader')
    ])).resolves.toEqual([
      expect.objectContaining({ slot: 'library' }),
      expect.objectContaining({ slot: 'reader' })
    ])
    expect(h.runtime.restore).toHaveBeenCalledOnce()
  })

  it('discards an in-flight response after account generation change and evicts at expiry', async () => {
    let release: ((value: unknown) => void) | undefined
    const response = new Promise<unknown>((resolve) => { release = resolve })
    const h = harness(free(), async () => response)
    const pending = h.controller.inventory('library')
    h.controller.background()
    release?.(fixture('eligible-library.json'))
    await expect(pending).resolves.toBeNull()
    expect(h.invalidated).toHaveBeenCalled()

    const expiring = harness(free(), async () => fixture('eligible-library.json'))
    await expiring.controller.inventory('library')
    expiring.setNow(REFRESH_AFTER)
    expiring.timers.at(-1)?.()
    expect(expiring.runtime.background).toHaveBeenCalledOnce()
    await expect(expiring.controller.openCta('library', 'https://example.com/podcast-reader')).rejects.toThrow('unavailable')
  })

  it('does not activate tokens approved after the app backgrounded', async () => {
    let approve: (() => void) | undefined
    const h = harness({ state: 'local' }, async () => null)
    h.deviceFlow.authorize.mockImplementation(async () => {
      await new Promise<void>((resolve) => { approve = resolve })
      return { access_token: 'a', refresh_token: 'r' }
    })
    const connecting = h.controller.connect()
    h.controller.background()
    approve?.()
    await expect(connecting).resolves.toEqual({ state: 'local', available: true })
    expect(h.runtime.acceptTokens).not.toHaveBeenCalled()
  })

  it('collapses a valid 204 without caching or third-party work', async () => {
    const h = harness(free(), async () => null)
    await expect(h.controller.inventory('reader')).resolves.toBeNull()
    await expect(h.controller.openCta('reader', 'https://example.com/premium')).rejects.toThrow('unavailable')
  })
})
