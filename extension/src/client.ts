import type { Pairing } from './storage'
import type { CookieJarInfo, HealthInfo, JobRecord } from '../../app/src/shared/types'

/**
 * Typed engine client for the extension (design decision 2: shapes import
 * from the app's comment-pinned mirror — the single source both TS
 * consumers share). The token travels ONLY in `Authorization` headers,
 * never URLs (ext-pairing spec); submissions carry
 * `requires_confirmation: false` because a deliberate click on the
 * user-installed, token-authed extension carries user intent (design
 * decision 6 / parent F10 scopes the confirm gate to the protocol channel).
 */

export class EngineRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(`engine request failed: ${status} ${detail}`)
  }
}

/**
 * Exchange a pairing code for the engine token — `POST /v1/pair/claim`, the
 * engine's single unauthenticated route. The `Content-Type` is set
 * explicitly: the engine's U3 gate rejects anything but `application/json`
 * (that requirement is what forces page-initiated requests into a CORS
 * preflight the engine never approves; the extension is host-permission
 * exempt and sends real JSON).
 */
export async function claimToken(
  port: number,
  code: string,
  fetchFn: typeof fetch = fetch
): Promise<string> {
  const res = await fetchFn(`http://127.0.0.1:${port}/v1/pair/claim`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code })
  })
  if (!res.ok) throw new EngineRequestError(res.status, await readDetail(res))
  const payload = (await res.json()) as { token: string }
  return payload.token
}

export class EngineClient {
  readonly baseUrl: string
  private readonly token: string
  private readonly fetchFn: typeof fetch

  constructor(pairing: Pairing, fetchFn: typeof fetch = fetch) {
    this.baseUrl = `http://127.0.0.1:${pairing.port}`
    this.token = pairing.token
    // Detach the receiver: `this.fetchFn(...)` would pass the client
    // instance as `this`, which the browser's window.fetch rejects
    // ("Illegal invocation" — caught by the e2e suite; node's fetch never
    // checks, so unit tests with injected mocks can't see it).
    this.fetchFn = (...args: Parameters<typeof fetch>) => fetchFn(...args)
  }

  // engine/app.py:257 (GET /v1/health — doubles as the pairing verifier)
  health(): Promise<HealthInfo> {
    return this.json('GET', '/v1/health')
  }

  // engine/app.py:317 (POST /v1/jobs) — always requires_confirmation: false
  // (ext-jobs spec: the confirm gate is scoped to the protocol channel).
  submitJob(source: string): Promise<JobRecord> {
    return this.json('POST', '/v1/jobs', {
      source,
      title: null,
      requires_confirmation: false
    })
  }

  // engine/app.py (GET /v1/jobs/{id}) — hydration source of truth
  getJob(jobId: string): Promise<JobRecord> {
    return this.json('GET', `/v1/jobs/${encodeURIComponent(jobId)}`)
  }

  // engine/app.py:497 (PUT /v1/cookies — write-only; 204)
  async putCookies(domain: string, jar: string): Promise<void> {
    await this.request('PUT', '/v1/cookies', { domain, jar })
  }

  // engine/app.py:512 (GET /v1/cookies — metadata only)
  listCookieJars(): Promise<CookieJarInfo[]> {
    return this.json('GET', '/v1/cookies')
  }

  // engine/app.py (GET /v1/events — SSE; popup-lifetime fetch stream)
  openEvents(signal: AbortSignal): Promise<Response> {
    return this.request('GET', '/v1/events', undefined, signal)
  }

  private async json<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await this.request(method, path, body)
    return (await res.json()) as T
  }

  private async request(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal
  ): Promise<Response> {
    const headers: Record<string, string> = { authorization: `Bearer ${this.token}` }
    if (body !== undefined) headers['content-type'] = 'application/json'
    const res = await this.fetchFn(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal
    })
    if (!res.ok) throw new EngineRequestError(res.status, await readDetail(res))
    return res
  }
}

async function readDetail(res: Response): Promise<string> {
  try {
    const payload = (await res.json()) as { detail?: unknown }
    if (typeof payload.detail === 'string') return payload.detail
    return JSON.stringify(payload)
  } catch {
    return res.statusText
  }
}
