import { describe, expect, it } from 'vitest'

import { EngineClient } from './engine-client'

describe('EngineClient subscription routes', () => {
  it('keeps capability and feed traffic on bearer-authenticated loopback routes', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchFn = (async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      calls.push({ url: String(input), init })
      if (init?.method === 'DELETE' || String(input).endsWith('/online-capabilities')) return new Response(null, { status: 204 })
      if (String(input).endsWith('/poll')) return new Response(JSON.stringify({ subscription: {}, discovered_count: 0, not_modified: true }), { status: 200 })
      return new Response(JSON.stringify([]), { status: 200 })
    }) as typeof fetch
    const client = new EngineClient(51234, 'engine-secret', fetchFn)
    await client.updateOnlineCapabilities({
      schema_version: 1,
      subject: 'usr_a',
      entitlement_revision: 7,
      flags_revision: 12,
      podcast_subscriptions: true,
      expires_at: '2026-08-03T00:05:00Z'
    })
    await client.listSubscriptions()
    await client.createSubscription('https://private.example/feed.xml')
    await client.pollSubscription('sub_0123456789abcdef0123456789abcdef')
    await client.deleteSubscription('sub_0123456789abcdef0123456789abcdef')

    expect(calls.every((call) => call.url.startsWith('http://127.0.0.1:51234/v1/'))).toBe(true)
    expect(calls.every((call) => (call.init?.headers as Record<string, string>).authorization === 'Bearer engine-secret')).toBe(true)
    expect(calls[2]?.url).not.toContain('private.example')
    expect(calls[2]?.init?.body).toBe(JSON.stringify({ feed_url: 'https://private.example/feed.xml' }))
  })
})
