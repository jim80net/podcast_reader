import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { EngineClient } from './engine-client'
import type { EmailOutboxStatusWire } from './email-contracts'

const fixture = <T>(name: string): T => JSON.parse(readFileSync(
  join(process.cwd(), '..', 'src', 'podcast_reader', 'engine', 'contracts', 'v1', 'email', name),
  'utf8'
)) as T

const status: EmailOutboxStatusWire = {
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

describe('EngineClient frozen email routes', () => {
  it('uses all five exact fixtures over bearer-authenticated loopback only', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchFn = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.endsWith('/v1/email/online-capability')) return new Response(null, { status: 204 })
      if (url.endsWith('/v1/email-outbox/claim')) {
        return new Response(JSON.stringify(fixture('claim.json')), { status: 200 })
      }
      if (url.includes('/email-preference')) {
        return new Response(JSON.stringify({
          subscription_id: 'sub_0123456789abcdef0123456789abcdef',
          enabled: true,
          consent_revision: 1
        }), { status: 200 })
      }
      if (url.endsWith('/v1/email-outbox')) return new Response(JSON.stringify([status]), { status: 200 })
      return new Response(JSON.stringify(status), { status: 200 })
    }) as typeof fetch
    const client = new EngineClient(51234, 'engine_secret', fetchFn)

    const capability = fixture<Parameters<EngineClient['updateEmailCapability']>[0]>('online-capability.json')
    await client.updateEmailCapability(capability)
    const manual = fixture<{ action_id: string; source_id: string }>('manual-create.json')
    await client.createManualEmail(manual.action_id, manual.source_id)
    await expect(client.claimEmailDelivery()).resolves.toEqual(fixture('claim.json'))
    await client.completeEmailDelivery(fixture('completion.json'))
    await client.releaseEmailDelivery(fixture('release.json'))
    await client.listEmailOutbox()

    expect(calls.map((call) => call.url)).toEqual([
      'http://127.0.0.1:51234/v1/email/online-capability',
      'http://127.0.0.1:51234/v1/email-outbox/manual',
      'http://127.0.0.1:51234/v1/email-outbox/claim',
      'http://127.0.0.1:51234/v1/email-outbox/complete',
      'http://127.0.0.1:51234/v1/email-outbox/release',
      'http://127.0.0.1:51234/v1/email-outbox'
    ])
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual(capability)
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual(manual)
    expect(JSON.parse(String(calls[3]?.init?.body))).toEqual(fixture('completion.json'))
    expect(JSON.parse(String(calls[4]?.init?.body))).toEqual(fixture('release.json'))
    for (const call of calls) {
      expect(call.init?.headers).toMatchObject({ authorization: 'Bearer engine_secret' })
    }
  })

  it('rejects an oversized claim stream before concatenating it', async () => {
    const oversized = new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(3 * 1024 * 1024 + 4097))
        controller.close()
      }
    })
    const client = new EngineClient(
      51234,
      'engine_secret',
      vi.fn(async () => new Response(oversized, { status: 200 })) as typeof fetch
    )
    await expect(client.claimEmailDelivery()).rejects.toThrow('engine response too large')
  })
})
