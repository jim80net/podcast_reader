import { describe, expect, it } from 'vitest'

import { EngineClient, EngineRequestError, EventStream } from './engine-client'
import type { JobRecord, PipelineEvent } from '../shared/types'

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

function client(respond: (req: Captured) => Response | Promise<Response>) {
  const { calls, fetchFn } = makeFetch(respond)
  return { calls, client: new EngineClient(51234, 'tok-123', fetchFn) }
}

describe('EngineClient', () => {
  it('targets 127.0.0.1:<port> and sends the bearer token on every request', async () => {
    const { calls, client: c } = client(() => json({ version: '0.3.0', token_fingerprint: 'f' }))
    await c.health()
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/health')
    expect(calls[0]?.headers['authorization']).toBe('Bearer tok-123')
  })

  it('submits jobs with requires_confirmation', async () => {
    const { calls, client: c } = client(() => json({ id: 'j1' }, 201))
    await c.submitJob({ source: 'https://e.com/v', title: null, requires_confirmation: true })
    expect(calls[0]?.method).toBe('POST')
    expect(calls[0]?.url).toContain('/v1/jobs')
    expect(JSON.parse(calls[0]?.body ?? '')).toEqual({
      source: 'https://e.com/v',
      title: null,
      requires_confirmation: true
    })
  })

  it('confirms and discards jobs on the right routes', async () => {
    const { calls, client: c } = client((req) =>
      req.method === 'DELETE' ? new Response(null, { status: 204 }) : json({ id: 'j1' })
    )
    await c.confirmJob('j1')
    await c.discardJob('j1')
    expect(calls[0]?.url).toContain('/v1/jobs/j1/confirm')
    expect(calls[0]?.method).toBe('POST')
    expect(calls[1]?.url).toContain('/v1/jobs/j1')
    expect(calls[1]?.method).toBe('DELETE')
  })

  it('URL-encodes path parameters', async () => {
    const { calls, client: c } = client(() => json({ id: 'x' }))
    await c.getJob('a/b c')
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/jobs/a%2Fb%20c')
  })

  it('fetches transcript HTML as text', async () => {
    const { calls, client: c } = client(() => new Response('<html>hi</html>', { status: 200 }))
    const html = await c.transcriptHtml('src1')
    expect(html).toBe('<html>hi</html>')
    expect(calls[0]?.url).toContain('/v1/transcripts/src1.html')
  })

  it('puts keys and never returns key material', async () => {
    const { calls, client: c } = client(() => new Response(null, { status: 204 }))
    await c.putKey('anthropic', 'sk-secret')
    expect(calls[0]?.method).toBe('PUT')
    expect(calls[0]?.url).toContain('/v1/keys')
    expect(JSON.parse(calls[0]?.body ?? '')).toEqual({ provider: 'anthropic', api_key: 'sk-secret' })
  })

  it('omits api_key from keys/test when not supplied (tests the stored key)', async () => {
    const { calls, client: c } = client(() => json({ ok: true, detail: null }))
    await c.testKey('openai')
    expect(JSON.parse(calls[0]?.body ?? '')).toEqual({ provider: 'openai' })
  })

  it('lists packs from /v1/packs', async () => {
    const payload = { hardware: { platform: 'win32', nvidia_gpu: true, gpu_names: [] }, packs: [] }
    const { calls, client: c } = client(() => json(payload))
    await expect(c.listPacks()).resolves.toEqual(payload)
    expect(calls[0]?.method).toBe('GET')
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/packs')
  })

  it('installs and uninstalls packs on the right routes, URL-encoding the id', async () => {
    const { calls, client: c } = client((req) =>
      req.method === 'POST' ? new Response(null, { status: 202 }) : new Response(null, { status: 204 })
    )
    await c.installPack('cuda-runtime')
    await c.uninstallPack('model/odd id')
    expect(calls[0]?.method).toBe('POST')
    expect(calls[0]?.url).toBe('http://127.0.0.1:51234/v1/packs/cuda-runtime/install')
    expect(calls[1]?.method).toBe('DELETE')
    expect(calls[1]?.url).toBe('http://127.0.0.1:51234/v1/packs/model%2Fodd%20id')
  })

  it('surfaces pack 409 details (installing / unavailable) as EngineRequestError', async () => {
    const { client: c } = client(() =>
      json({ detail: "pack 'diarization' has no published artifact yet and cannot be installed" }, 409)
    )
    await expect(c.installPack('diarization')).rejects.toMatchObject({
      status: 409,
      detail: "pack 'diarization' has no published artifact yet and cannot be installed"
    })
  })

  it('raises EngineRequestError with the detail on non-2xx', async () => {
    const { client: c } = client(() => json({ detail: 'job not found' }, 404))
    await expect(c.getJob('nope')).rejects.toThrowError(EngineRequestError)
    await expect(c.getJob('nope')).rejects.toMatchObject({ status: 404, detail: 'job not found' })
  })

  it('accepts the 202 shutdown response with an empty body', async () => {
    const { calls, client: c } = client(() => new Response(null, { status: 202 }))
    await expect(c.shutdown()).resolves.toBeUndefined()
    expect(calls[0]?.url).toContain('/v1/shutdown')
    expect(calls[0]?.method).toBe('POST')
  })

  it('aborts a hung shutdown POST so quit never stalls before the bounded wait', async () => {
    const hangingFetch = ((_input: Parameters<typeof fetch>[0], init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(init.signal?.reason ?? new Error('aborted'))
        )
      })) as typeof fetch
    const c = new EngineClient(51234, 'tok-123', hangingFetch)
    await expect(c.shutdown({ timeoutMs: 20 })).rejects.toThrow()
  })
})

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    }
  })
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

const event = (message: string): PipelineEvent => ({
  kind: 'warning',
  step: null,
  message,
  data: {}
})

describe('EventStream', () => {
  it('hydrates from GET /v1/jobs after connecting, then forwards events', async () => {
    const jobs: JobRecord[] = []
    const order: string[] = []
    let connections = 0
    const { fetchFn } = makeFetch((req) => {
      if (req.url.includes('/v1/events')) {
        connections += 1
        if (connections > 1) return new Response(null, { status: 401 }) // stop after one pass
        return sseResponse([`data: ${JSON.stringify(event('e1'))}\n\n`])
      }
      return json(jobs)
    })
    const seen: string[] = []
    const stream = new EventStream(new EngineClient(1, 't', fetchFn), {
      onEvent: (e) => {
        order.push('event')
        seen.push(e.message)
      },
      onHydrate: () => order.push('hydrate')
    })
    await stream.runOnce()
    expect(order[0]).toBe('hydrate')
    expect(seen).toEqual(['e1'])
  })

  it('reconnects with backoff after a dropped stream', async () => {
    let connections = 0
    const { fetchFn } = makeFetch((req) => {
      if (req.url.includes('/v1/events')) {
        connections += 1
        if (connections === 1) return sseResponse([]) // immediately-closed stream
        return sseResponse([`data: ${JSON.stringify(event('after-reconnect'))}\n\n`])
      }
      return json([])
    })
    const slept: number[] = []
    const seen: string[] = []
    let hydrations = 0
    const stream = new EventStream(
      new EngineClient(1, 't', fetchFn),
      {
        onEvent: (e) => {
          seen.push(e.message)
          stream.abort() // end the test after the reconnect delivers
        },
        onHydrate: () => {
          hydrations += 1
        }
      },
      {
        backoffMs: [7, 13],
        sleep: async (ms) => {
          slept.push(ms)
        }
      }
    )
    await stream.run()
    expect(connections).toBe(2)
    expect(slept).toEqual([7])
    expect(hydrations).toBe(2) // hydrate after EVERY (re)connect
    expect(seen).toEqual(['after-reconnect'])
  })

  it('escalates the backoff and caps it at the last step', async () => {
    let attempts = 0
    const { fetchFn } = makeFetch((req) => {
      if (req.url.includes('/v1/events')) {
        attempts += 1
        throw new Error('connection refused')
      }
      return json([])
    })
    const slept: number[] = []
    const stream = new EventStream(
      new EngineClient(1, 't', fetchFn),
      { onEvent: () => {}, onHydrate: () => {} },
      {
        backoffMs: [5, 10],
        sleep: async (ms) => {
          slept.push(ms)
          if (slept.length === 4) stream.abort()
        }
      }
    )
    await stream.run()
    expect(attempts).toBe(4)
    expect(slept).toEqual([5, 10, 10, 10])
  })

  it('stops without reconnecting when aborted', async () => {
    let connections = 0
    const { fetchFn } = makeFetch((req) => {
      if (req.url.includes('/v1/events')) {
        connections += 1
        return sseResponse([`data: ${JSON.stringify(event('x'))}\n\n`])
      }
      return json([])
    })
    const stream = new EventStream(new EngineClient(1, 't', fetchFn), {
      onEvent: () => stream.abort(),
      onHydrate: () => {}
    })
    await stream.run()
    expect(connections).toBe(1)
  })
})
