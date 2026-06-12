import { describe, expect, it } from 'vitest'

import { claimToken, EngineClient, EngineRequestError } from './client'

interface Captured {
  url: string
  method: string
  headers: Record<string, string>
  body: string | null
}

function makeFetch(
  respond: (req: Captured) => Response | Promise<Response>
): { calls: Captured[]; fetchFn: typeof fetch } {
  const calls: Captured[] = []
  const fetchFn = (async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
    const req: Captured = {
      url: String(input),
      method: init?.method ?? 'GET',
      headers: Object.fromEntries(
        Object.entries((init?.headers ?? {}) as Record<string, string>).map(([k, v]) => [
          k.toLowerCase(),
          v
        ])
      ),
      body: typeof init?.body === 'string' ? init.body : null
    }
    calls.push(req)
    return respond(req)
  }) as typeof fetch
  return { calls, fetchFn }
}

const json = (payload: unknown, status = 200): Response =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' }
  })

const pairing = { port: 51234, token: 'tok-123' }

function client(respond: (req: Captured) => Response | Promise<Response>) {
  const { calls, fetchFn } = makeFetch(respond)
  return { calls, client: new EngineClient(pairing, fetchFn) }
}

describe('claimToken', () => {
  it('POSTs the code with an explicit application/json content type (per U3)', async () => {
    const { calls, fetchFn } = makeFetch(() => json({ token: 'tok-9' }))
    await expect(claimToken(51234, 'ABC234', fetchFn)).resolves.toBe('tok-9')
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/pair/claim')
    expect(calls[0]?.method).toBe('POST')
    expect(calls[0]?.headers['content-type']).toBe('application/json')
    expect(JSON.parse(calls[0]?.body ?? '')).toEqual({ code: 'ABC234' })
    // The claim is the route's whole auth story: no Authorization header.
    expect(calls[0]?.headers['authorization']).toBeUndefined()
  })

  it('surfaces the uniform 403 as a status-carrying error', async () => {
    const { fetchFn } = makeFetch(() => json({ detail: 'pairing claim rejected' }, 403))
    await expect(claimToken(51234, 'WRONG2', fetchFn)).rejects.toMatchObject({
      status: 403,
      detail: 'pairing claim rejected'
    })
  })
})

describe('EngineClient', () => {
  it('targets the paired port and sends the token only as a bearer header', async () => {
    const { calls, client: c } = client(() => json({ version: '0.3.0', token_fingerprint: 'f' }))
    await c.health()
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/health')
    expect(calls[0]?.headers['authorization']).toBe('Bearer tok-123')
    expect(calls[0]?.url).not.toContain('tok-123') // never in URLs
  })

  it('submits jobs with requires_confirmation: false (design decision 6)', async () => {
    const { calls, client: c } = client(() => json({ id: 'j1' }, 201))
    await c.submitJob('https://x.com/user/status/1')
    expect(JSON.parse(calls[0]?.body ?? '')).toEqual({
      source: 'https://x.com/user/status/1',
      title: null,
      requires_confirmation: false
    })
  })

  it('fetches single job records with URL-encoded ids', async () => {
    const { calls, client: c } = client(() => json({ id: 'a/b' }))
    await c.getJob('a/b')
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/jobs/a%2Fb')
  })

  it('PUTs cookie jars and resolves on 204', async () => {
    const { calls, client: c } = client(() => new Response(null, { status: 204 }))
    await c.putCookies('example.com', '# Netscape HTTP Cookie File\n…')
    expect(calls[0]?.method).toBe('PUT')
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/cookies')
    expect(JSON.parse(calls[0]?.body ?? '')).toEqual({
      domain: 'example.com',
      jar: '# Netscape HTTP Cookie File\n…'
    })
  })

  it('raises EngineRequestError with the engine detail on non-2xx', async () => {
    const { client: c } = client(() => json({ detail: 'unauthorized' }, 401))
    await expect(c.health()).rejects.toThrowError(EngineRequestError)
    await expect(c.health()).rejects.toMatchObject({ status: 401, detail: 'unauthorized' })
  })

  it('opens the events stream with header auth and an abort signal', async () => {
    let sawSignal: AbortSignal | undefined
    const { calls, fetchFn } = makeFetch(() => new Response('', { status: 200 }))
    const wrapped = (async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      sawSignal = init?.signal ?? undefined
      return fetchFn(input, init)
    }) as typeof fetch
    const c = new EngineClient(pairing, wrapped)
    const controller = new AbortController()
    await c.openEvents(controller.signal)
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/events')
    expect(calls[0]?.headers['authorization']).toBe('Bearer tok-123')
    expect(sawSignal).toBe(controller.signal)
  })
})
