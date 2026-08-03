import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PremiumCredentialStore } from './credentials'
import type { PremiumCredentials } from './credentials'
import { reduceEntitlement } from './contracts'
import { PremiumDeviceFlow } from './device-flow'
import { PremiumOrigin } from './origin'
import { PremiumRuntime } from './runtime'
import { PremiumRequestError, PremiumTransport } from './transport'
import type { SafeStorageLike } from '../vault'

const safe: SafeStorageLike = { isEncryptionAvailable: () => true, encryptString: (s) => Buffer.from(`x${s}`), decryptString: (b) => b.toString().slice(1) }
const entitlement = (tier: 'free' | 'premium', subject = 'usr_one') => ({ schema_version: 1, subject, tier, entitlement: { source: tier === 'free' ? 'none' : 'test_purchase', revision: 1 }, capabilities: { ad_policy: tier === 'free' ? 'house' : 'none', podcast_subscriptions: tier === 'premium', transcript_email: false, mobile_ad_free: tier === 'premium', topic_corpus: false }, flags_revision: 1, evaluated_at: '2020-01-01T00:00:00Z', refresh_after: '2099-01-01T00:05:00Z' })
let dirs: string[] = []
afterEach(() => { for (const dir of dirs) rmSync(dir, { recursive: true, force: true }); dirs = [] })

describe('premium desktop boundary', () => {
  it('consumes the backend-owned entitlement fixtures without a competing copy', () => {
    const contracts = join(process.cwd(), '..', 'services', 'premium', 'contracts')
    const free = JSON.parse(readFileSync(join(contracts, 'entitlements-v1-free.json'), 'utf8')) as unknown
    const premium = JSON.parse(readFileSync(join(contracts, 'entitlements-v1-premium.json'), 'utf8')) as unknown
    const now = Date.parse('2026-08-02T00:02:00Z')
    expect(reduceEntitlement(free, 'usr_free_fixture', now)).toMatchObject({ state: 'online-free', adPolicy: 'none' })
    expect(reduceEntitlement(premium, 'usr_premium_fixture', now)).toMatchObject({ state: 'online-premium' })
  })

  it('accepts admin projections but rejects future or capability-inconsistent truth', () => {
    const now = Date.parse('2026-08-03T00:00:00Z')
    const admin = { ...entitlement('premium'), entitlement: { source: 'admin', revision: 2 }, evaluated_at: '2026-08-02T23:59:00Z', refresh_after: '2026-08-03T00:04:00Z' }
    expect(reduceEntitlement(admin, 'usr_one', now)).toMatchObject({ state: 'online-premium' })
    expect(reduceEntitlement({ ...admin, evaluated_at: '2026-08-03T00:01:00Z', refresh_after: '2026-08-03T00:06:00Z' }, 'usr_one', now)).toMatchObject({ state: 'online-premium' })
    expect(() => reduceEntitlement({ ...admin, evaluated_at: '2026-08-03T00:06:00Z', refresh_after: '2026-08-03T00:11:00Z' }, 'usr_one', now)).toThrow('stale premium contract')
    const badFree = entitlement('free')
    badFree.capabilities.transcript_email = true
    expect(() => reduceEntitlement(badFree, 'usr_one', now)).toThrow('invalid premium contract')
  })

  it('accepts only an exact configured HTTPS origin', () => {
    expect(PremiumOrigin.fromTrustedConfiguration('https://premium.example').resolve('/v1/me/entitlements')).toBe('https://premium.example/v1/me/entitlements')
    expect(() => PremiumOrigin.fromTrustedConfiguration('https://premium.example').resolve('/v1/../../admin')).toThrow('invalid premium route')
    for (const bad of ['http://premium.example', 'https://premium.example/', 'https://u:p@premium.example', 'https://premium.example/path']) expect(() => PremiumOrigin.fromTrustedConfiguration(bad)).toThrow('invalid premium origin')
  })

  it('stores only encrypted refresh credentials and falls back to session memory', () => {
    const dir = mkdtempSync(join(tmpdir(), 'premium-store-')); dirs.push(dir); const path = join(dir, 'account.json')
    new PremiumCredentialStore(path, safe).set({ subject: 'usr_one', refreshToken: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })
    expect(readFileSync(path, 'utf8')).not.toContain('refresh_token_abcdefghijklmnopqrstuvwxyz')
    const memory = new PremiumCredentialStore(join(dir, 'memory.json'), { ...safe, isEncryptionAvailable: () => false })
    memory.set({ subject: 'usr_one', refreshToken: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })
    expect(memory.mode).toBe('session-memory')
  })

  it('keeps credential updates transactional and invalidates stale disk state in memory mode', () => {
    const dir = mkdtempSync(join(tmpdir(), 'premium-store-')); dirs.push(dir); const path = join(dir, 'account.json')
    const store = new PremiumCredentialStore(path, safe)
    store.set({ subject: 'usr_one', refreshToken: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })
    const broken = { ...safe, encryptString: () => { throw new Error('keychain unavailable') } }
    const brokenStore = new PremiumCredentialStore(path, broken)
    expect(() => brokenStore.set({ subject: 'usr_two', refreshToken: 'refresh_token_two_abcdefghijklmnop' })).toThrow('keychain unavailable')
    expect(brokenStore.get()?.subject).toBe('usr_one')
    new PremiumCredentialStore(path, { ...safe, isEncryptionAvailable: () => false })
    expect(existsSync(path)).toBe(false)
  })

  it('rejects unknown credential-envelope fields', () => {
    const dir = mkdtempSync(join(tmpdir(), 'premium-store-')); dirs.push(dir); const path = join(dir, 'account.json')
    writeFileSync(path, JSON.stringify({ schema_version: 1, ciphertext: null, surprise: true }))
    expect(new PremiumCredentialStore(path, safe).get()).toBeNull()
    expect(existsSync(path)).toBe(false)
  })

  it('keeps premium requests credential-separated and rejects redirects', async () => {
    const fetchFn = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => { void init; return new Response(JSON.stringify(entitlement('free')), { status: 200 }) })
    const transport = new PremiumTransport(PremiumOrigin.fromTrustedConfiguration('https://premium.example'), fetchFn as typeof fetch)
    await transport.entitlement('access_secret')
    expect(fetchFn.mock.calls[0]?.[1]).toMatchObject({ redirect: 'error', credentials: 'omit', referrerPolicy: 'no-referrer' })
    expect((fetchFn.mock.calls[0]?.[1]?.headers as Record<string, string>).Authorization).toBe('Bearer access_secret')
  })

  it('rejects oversized streamed responses before parsing', async () => {
    const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(65 * 1024)); controller.close() } })
    const transport = new PremiumTransport(PremiumOrigin.fromTrustedConfiguration('https://premium.example'), vi.fn(async () => new Response(body)) as typeof fetch)
    await expect(transport.entitlement('access_token_abcdefghijklmnopqrstuvwxyz')).rejects.toThrow('premium response too large')
  })

  it('runs device authorization in the system browser with bounded polling', async () => {
    const transport = {
      startDeviceAuthorization: vi.fn(async () => ({ device_code: 'device_code_abcdefghijklmnopqrstuvwxyz', user_code: 'ABCD-EFGH', verification_uri: 'https://premium.example/device', expires_in: 60, interval: 1 })),
      ownsExternalUrl: vi.fn(() => true),
      pollDeviceToken: vi.fn().mockRejectedValueOnce(new PremiumRequestError(400, 'authorization_pending')).mockResolvedValue({ access_token: 'access_token_abcdefghijklmnopqrstuvwxyz', token_type: 'Bearer', expires_in: 900, refresh_token: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })
    }
    let now = 0
    const openExternal = vi.fn(async () => undefined)
    const flow = new PremiumDeviceFlow(transport as unknown as PremiumTransport, { openExternal, sleep: async (ms) => { now += ms }, now: () => now })
    await expect(flow.authorize()).resolves.toMatchObject({ token_type: 'Bearer' })
    expect(openExternal).toHaveBeenCalledWith('https://premium.example/device')
    expect(transport.pollDeviceToken).toHaveBeenCalledTimes(2)
  })

  it('never polls after a device authorization deadline', async () => {
    const transport = { startDeviceAuthorization: vi.fn(async () => ({ device_code: 'device_code_abcdefghijklmnopqrstuvwxyz', user_code: 'ABCD-EFGH', verification_uri: 'https://premium.example/device', expires_in: 1, interval: 60 })), ownsExternalUrl: vi.fn(() => true), pollDeviceToken: vi.fn() }
    let now = 0
    const flow = new PremiumDeviceFlow(transport as unknown as PremiumTransport, { openExternal: async () => undefined, sleep: async (ms) => { now += ms }, now: () => now })
    await expect(flow.authorize()).rejects.toThrow('device authorization expired')
    expect(transport.pollDeviceToken).not.toHaveBeenCalled()
  })

  it('evicts state on background and fails closed on a subject mismatch', async () => {
    let stored: PremiumCredentials | null = null
    const store = { get: vi.fn(() => stored), set: vi.fn(), mode: 'session-memory' as const }
    const transport = { entitlement: vi.fn(async () => entitlement('free')), refresh: vi.fn() }
    const runtime = new PremiumRuntime(transport as unknown as PremiumTransport, store as unknown as PremiumCredentialStore)
    await expect(runtime.acceptTokens({ access_token: 'access', refresh_token: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })).resolves.toMatchObject({ state: 'online-free' })
    expect(runtime.background()).toEqual({ state: 'local' })
    stored = { subject: 'usr_one', refreshToken: 'refresh_token_abcdefghijklmnopqrstuvwxyz' }
    expect(runtime.background()).toEqual({ state: 'online-unavailable' })
    transport.entitlement.mockResolvedValue({ ...entitlement('free'), subject: '' })
    await expect(runtime.acceptTokens({ access_token: 'a', refresh_token: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })).rejects.toThrow('invalid premium contract')
    expect(runtime.state).toEqual({ state: 'online-unavailable' })
  })

  it('cannot restore an older account after sign-out wins the generation race', async () => {
    let resolveEntitlement!: (value: unknown) => void
    const pending = new Promise<unknown>((resolve) => { resolveEntitlement = resolve })
    const store = { get: vi.fn(() => ({ subject: 'usr_one', refreshToken: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })), set: vi.fn(), mode: 'session-memory' as const }
    const transport = { refresh: vi.fn(async () => ({ access_token: 'access_token_abcdefghijklmnopqrstuvwxyz', refresh_token: 'refresh_token_rotated_abcdefghijklmnop', token_type: 'Bearer', expires_in: 900 })), entitlement: vi.fn(() => pending) }
    const runtime = new PremiumRuntime(transport as unknown as PremiumTransport, store as unknown as PremiumCredentialStore)
    const restoring = runtime.restore()
    await Promise.resolve()
    runtime.signOut()
    resolveEntitlement(entitlement('free'))
    await restoring
    expect(runtime.state).toEqual({ state: 'local' })
    expect(runtime.bearer).toBeNull()
  })

  it('clears memory even when sign-out persistence fails', async () => {
    let fail = false
    const store = { get: vi.fn(() => null), set: vi.fn(() => { if (fail) throw new Error('disk failed') }), mode: 'encrypted' as const }
    const transport = { entitlement: vi.fn(async () => entitlement('free')) }
    const runtime = new PremiumRuntime(transport as unknown as PremiumTransport, store as unknown as PremiumCredentialStore)
    await runtime.acceptTokens({ access_token: 'access_token_abcdefghijklmnopqrstuvwxyz', refresh_token: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })
    fail = true
    expect(() => runtime.signOut()).toThrow('disk failed')
    expect(runtime.state).toEqual({ state: 'local' })
    expect(runtime.bearer).toBeNull()
  })
})
