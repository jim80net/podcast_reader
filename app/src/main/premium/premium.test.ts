import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PremiumCredentialStore } from './credentials'
import { reduceEntitlement } from './contracts'
import { PremiumDeviceFlow } from './device-flow'
import { PremiumOrigin } from './origin'
import { PremiumRuntime } from './runtime'
import { PremiumRequestError, PremiumTransport } from './transport'
import type { SafeStorageLike } from '../vault'

const safe: SafeStorageLike = { isEncryptionAvailable: () => true, encryptString: (s) => Buffer.from(`x${s}`), decryptString: (b) => b.toString().slice(1) }
const entitlement = (tier: 'free' | 'premium', subject = 'usr_one') => ({ schema_version: 1, subject, tier, entitlement: { source: tier === 'free' ? 'none' : 'test_purchase', revision: 1 }, capabilities: { ad_policy: tier === 'free' ? 'house' : 'none', podcast_subscriptions: tier === 'premium', transcript_email: false, mobile_ad_free: tier === 'premium', topic_corpus: false }, flags_revision: 1, evaluated_at: '2099-01-01T00:00:00Z', refresh_after: '2099-01-01T00:05:00Z' })
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

  it('accepts only an exact configured HTTPS origin', () => {
    expect(PremiumOrigin.fromTrustedConfiguration('https://premium.example').resolve('/v1/me/entitlements')).toBe('https://premium.example/v1/me/entitlements')
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

  it('keeps premium requests credential-separated and rejects redirects', async () => {
    const fetchFn = vi.fn(async (_url: string | URL | Request, init?: RequestInit) => { void init; return new Response(JSON.stringify(entitlement('free')), { status: 200 }) })
    const transport = new PremiumTransport(PremiumOrigin.fromTrustedConfiguration('https://premium.example'), fetchFn as typeof fetch)
    await transport.entitlement('access_secret')
    expect(fetchFn.mock.calls[0]?.[1]).toMatchObject({ redirect: 'error', credentials: 'omit', referrerPolicy: 'no-referrer' })
    expect((fetchFn.mock.calls[0]?.[1]?.headers as Record<string, string>).Authorization).toBe('Bearer access_secret')
  })

  it('runs device authorization in the system browser with bounded polling', async () => {
    const transport = {
      startDeviceAuthorization: vi.fn(async () => ({ device_code: 'device', user_code: 'ABCD', verification_uri: 'https://premium.example/device', expires_in: 60, interval: 1 })),
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

  it('evicts state on background and fails closed on a subject mismatch', async () => {
    const store = { get: vi.fn(() => null), set: vi.fn(), mode: 'session-memory' as const }
    const transport = { entitlement: vi.fn(async () => entitlement('free')), refresh: vi.fn() }
    const runtime = new PremiumRuntime(transport as unknown as PremiumTransport, store as unknown as PremiumCredentialStore)
    await expect(runtime.acceptTokens({ access_token: 'access', refresh_token: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })).resolves.toMatchObject({ state: 'online-free' })
    expect(runtime.background()).toEqual({ state: 'local' })
    transport.entitlement.mockResolvedValue({ ...entitlement('free'), subject: '' })
    await expect(runtime.acceptTokens({ access_token: 'a', refresh_token: 'refresh_token_abcdefghijklmnopqrstuvwxyz' })).rejects.toThrow('invalid premium contract')
    expect(runtime.state).toEqual({ state: 'online-unavailable' })
  })
})
