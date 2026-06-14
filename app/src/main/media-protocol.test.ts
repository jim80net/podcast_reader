import { describe, expect, it } from 'vitest'

import { SOURCE_ID_PATTERN, createMediaProtocolHandler } from './media-protocol'

/**
 * The privileged `app://media/<source_id>` handler (task 6.2, app-shell spec):
 * validates the id, adds the bearer token the renderer never holds, forwards
 * Range, and streams the engine response verbatim — only ever to the loopback
 * engine.
 */

const VALID_ID = 'a'.repeat(64)

interface Captured {
  url: string
  method: string
  headers: Record<string, string>
}

function makeFetch(respond: (req: Captured) => Response): {
  calls: Captured[]
  fetchFn: typeof fetch
} {
  const calls: Captured[] = []
  const fetchFn = (async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
    const headers: Record<string, string> = {}
    for (const [k, v] of Object.entries((init?.headers ?? {}) as Record<string, string>)) {
      headers[k.toLowerCase()] = v
    }
    const req: Captured = { url: String(input), method: init?.method ?? 'GET', headers }
    calls.push(req)
    return respond(req)
  }) as typeof fetch
  return { calls, fetchFn }
}

/** An access provider with a ready engine. */
function readyAccess(): () => { baseUrl: string; token: string } | null {
  return () => ({ baseUrl: 'http://127.0.0.1:51234', token: 'tok-9' })
}

describe('SOURCE_ID_PATTERN', () => {
  it('matches a 64-char lowercase sha256 hex and nothing else', () => {
    expect(SOURCE_ID_PATTERN.test(VALID_ID)).toBe(true)
    expect(SOURCE_ID_PATTERN.test('A'.repeat(64))).toBe(false) // uppercase
    expect(SOURCE_ID_PATTERN.test('a'.repeat(63))).toBe(false) // too short
    expect(SOURCE_ID_PATTERN.test('a'.repeat(65))).toBe(false) // too long
    expect(SOURCE_ID_PATTERN.test('../etc/passwd')).toBe(false) // traversal
    expect(SOURCE_ID_PATTERN.test('z'.repeat(64))).toBe(false) // non-hex
  })
})

describe('createMediaProtocolHandler', () => {
  it('proxies a valid id to the loopback engine media route with the bearer token', async () => {
    const { calls, fetchFn } = makeFetch(() => new Response('bytes', { status: 200 }))
    const handler = createMediaProtocolHandler(readyAccess(), fetchFn)
    const res = await handler(new Request(`app://media/${VALID_ID}`))
    expect(res.status).toBe(200)
    expect(calls[0]?.url).toBe(`http://127.0.0.1:51234/v1/media/${VALID_ID}`)
    expect(calls[0]?.headers['authorization']).toBe('Bearer tok-9')
  })

  it('forwards the inbound Range header and returns the 206 verbatim', async () => {
    const { calls, fetchFn } = makeFetch(
      () =>
        new Response('partial', {
          status: 206,
          headers: { 'content-range': 'bytes 0-6/100', 'accept-ranges': 'bytes' }
        })
    )
    const handler = createMediaProtocolHandler(readyAccess(), fetchFn)
    const res = await handler(
      new Request(`app://media/${VALID_ID}`, { headers: { Range: 'bytes=0-6' } })
    )
    expect(calls[0]?.headers['range']).toBe('bytes=0-6')
    expect(res.status).toBe(206)
    expect(res.headers.get('content-range')).toBe('bytes 0-6/100')
  })

  it('rejects a malformed id without contacting the engine', async () => {
    const { calls, fetchFn } = makeFetch(() => new Response('bytes'))
    const handler = createMediaProtocolHandler(readyAccess(), fetchFn)
    const res = await handler(new Request('app://media/not-a-valid-id'))
    expect(res.status).toBe(400)
    expect(calls).toHaveLength(0)
  })

  it('rejects a non-media host without contacting the engine', async () => {
    const { calls, fetchFn } = makeFetch(() => new Response('bytes'))
    const handler = createMediaProtocolHandler(readyAccess(), fetchFn)
    const res = await handler(new Request(`app://evil/${VALID_ID}`))
    expect(res.status).toBe(404)
    expect(calls).toHaveLength(0)
  })

  it('returns 503 when the engine is not ready', async () => {
    const { calls, fetchFn } = makeFetch(() => new Response('bytes'))
    const handler = createMediaProtocolHandler(() => null, fetchFn)
    const res = await handler(new Request(`app://media/${VALID_ID}`))
    expect(res.status).toBe(503)
    expect(calls).toHaveLength(0)
  })

  it('propagates a 404 from the engine (media not ready) verbatim', async () => {
    const { fetchFn } = makeFetch(() => new Response('not found', { status: 404 }))
    const handler = createMediaProtocolHandler(readyAccess(), fetchFn)
    const res = await handler(new Request(`app://media/${VALID_ID}`))
    expect(res.status).toBe(404)
  })
})
