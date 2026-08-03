import { SseParser } from './sse'
import {
  validateEmailClaim,
  validateEmailOutboxStatus,
  validateEmailPreference
} from './email-contracts'
import type {
  EmailCapabilitySnapshot,
  EmailClaim,
  EmailDeliveryErrorCode,
  EmailOutboxStatusWire,
  EmailPreferenceWire
} from './email-contracts'
import type {
  CookieJarInfo,
  EngineSettings,
  HealthInfo,
  JobRecord,
  JobSubmission,
  KeyTestResult,
  LibraryEntry,
  LibrarySearchAttempt,
  LibrarySearchResponse,
  MediaInfo,
  PacksResponse,
  PairStartResponse,
  PipelineEvent,
  ProviderInfo,
  SettingsUpdate
} from '../shared/types'

/**
 * Typed, bearer-authenticated client for the engine's `/v1` surface
 * (`engine/app.py`). Lives exclusively in the main process — the token never
 * reaches the renderer (design decision 4). The engine binds 127.0.0.1 only.
 */

/** Bound on the shutdown POST — small, so quit falls through to wait/force-kill fast. */
const SHUTDOWN_TIMEOUT_MS = 2000

export interface OnlineCapabilitySnapshot {
  schema_version: 1
  subject: string
  entitlement_revision: number
  flags_revision: number
  podcast_subscriptions: boolean
  expires_at: string
}

export interface EngineSubscriptionRecord {
  id: string
  feed_url: string
  enabled: boolean
  title: string | null
  normalized_origin: string
  etag: string | null
  last_modified: string | null
  last_checked_at: string | null
  next_check_at: string | null
  last_error: string | null
  last_error_at: string | null
  created_at: string
  updated_at: string
}

export interface EngineSubscriptionPollResult {
  subscription: EngineSubscriptionRecord
  discovered_count: number
  not_modified: boolean
}

export class EngineRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(`engine request failed: ${status} ${detail}`)
  }
}

export class EngineClient {
  readonly baseUrl: string

  constructor(
    port: number,
    private readonly token: string,
    private readonly fetchFn: typeof fetch = fetch
  ) {
    this.baseUrl = `http://127.0.0.1:${port}`
  }

  // engine/app.py:188 (GET /v1/health)
  health(): Promise<HealthInfo> {
    return this.json('GET', '/v1/health')
  }

  // engine/app.py:196 (POST /v1/shutdown — 202 then exit). Tightly bounded:
  // quit awaits this POST before its bounded exit-wait, so a hung request
  // must abort rather than stall the whole quit sequence.
  async shutdown(opts: { timeoutMs?: number } = {}): Promise<void> {
    const timeoutMs = opts.timeoutMs ?? SHUTDOWN_TIMEOUT_MS
    await this.request('POST', '/v1/shutdown', undefined, AbortSignal.timeout(timeoutMs))
  }

  // engine/app.py:207 (POST /v1/jobs)
  submitJob(body: JobSubmission): Promise<JobRecord> {
    return this.json('POST', '/v1/jobs', body)
  }

  // engine/app.py:213 (GET /v1/jobs)
  listJobs(): Promise<JobRecord[]> {
    return this.json('GET', '/v1/jobs')
  }

  // engine/app.py:217 (GET /v1/jobs/{id})
  getJob(jobId: string): Promise<JobRecord> {
    return this.json('GET', `/v1/jobs/${encodeURIComponent(jobId)}`)
  }

  // engine/app.py:224 (POST /v1/jobs/{id}/confirm — 409 from non-awaiting states)
  confirmJob(jobId: string): Promise<JobRecord> {
    return this.json('POST', `/v1/jobs/${encodeURIComponent(jobId)}/confirm`)
  }

  // engine/app.py:234 (DELETE /v1/jobs/{id} — awaiting-confirmation only)
  async discardJob(jobId: string): Promise<void> {
    await this.request('DELETE', `/v1/jobs/${encodeURIComponent(jobId)}`)
  }

  // engine/app.py:262 (GET /v1/library)
  listLibrary(): Promise<LibraryEntry[]> {
    return this.json('GET', '/v1/library')
  }

  async searchLibrary(query: string): Promise<LibrarySearchAttempt> {
    try {
      return await this.json<LibrarySearchResponse>('POST', '/v1/search', { query })
    } catch (error) {
      if (error instanceof EngineRequestError && error.status === 429) return { busy: true }
      throw error
    }
  }

  // engine/app.py:266 (GET /v1/transcripts/{source_id}.html)
  async transcriptHtml(sourceId: string): Promise<string> {
    const res = await this.request('GET', `/v1/transcripts/${encodeURIComponent(sourceId)}.html`)
    return res.text()
  }

  // engine/app.py (GET /v1/media/{id}/info — playback classification + prep
  // status). Media BYTES never cross this client; only this metadata does —
  // the bytes load directly via the main-mediated app:// scheme (media-protocol.ts).
  mediaInfo(sourceId: string): Promise<MediaInfo> {
    return this.json('GET', `/v1/media/${encodeURIComponent(sourceId)}/info`)
  }

  // engine/app.py:347 (GET /v1/settings)
  getSettings(): Promise<EngineSettings> {
    return this.json('GET', '/v1/settings')
  }

  // engine/app.py:351 (PUT /v1/settings — validates at write time)
  putSettings(body: SettingsUpdate): Promise<EngineSettings> {
    return this.json('PUT', '/v1/settings', body)
  }

  // engine/app.py:273 (PUT /v1/keys — write-only; "" clears, restoring env fallback)
  async putKey(provider: string, apiKey: string): Promise<void> {
    await this.request('PUT', '/v1/keys', { provider, api_key: apiKey })
  }

  // engine/app.py:288 (POST /v1/keys/test — api_key absent tests the stored key)
  testKey(provider: string, apiKey?: string): Promise<KeyTestResult> {
    const body: { provider: string; api_key?: string } = { provider }
    if (apiKey !== undefined) body.api_key = apiKey
    return this.json('POST', '/v1/keys/test', body)
  }

  // engine/app.py:331 (GET /v1/providers — availability booleans, never key material)
  listProviders(): Promise<ProviderInfo[]> {
    return this.json('GET', '/v1/providers')
  }

  // engine/app.py:283 (GET /v1/packs — hardware block + per-pack statuses)
  listPacks(): Promise<PacksResponse> {
    return this.json('GET', '/v1/packs')
  }

  // engine/app.py:289 (POST /v1/packs/{id}/install — 202 always when installable,
  // idempotent; 404 unknown, 409 unpublished/platform-gated)
  async installPack(packId: string): Promise<void> {
    await this.request('POST', `/v1/packs/${encodeURIComponent(packId)}/install`)
  }

  // engine/app.py:304 (DELETE /v1/packs/{id} — 409 only while installing, per S1)
  async uninstallPack(packId: string): Promise<void> {
    await this.request('DELETE', `/v1/packs/${encodeURIComponent(packId)}`)
  }

  // engine/app.py:265 (POST /v1/pair — bearer-authed mint; the code rides
  // only this response and the Settings display, never any file or log)
  mintPairing(): Promise<PairStartResponse> {
    return this.json('POST', '/v1/pair')
  }

  // engine/app.py:512 (GET /v1/cookies — metadata only, never jar content)
  listCookieJars(): Promise<CookieJarInfo[]> {
    return this.json('GET', '/v1/cookies')
  }

  updateOnlineCapabilities(snapshot: OnlineCapabilitySnapshot): Promise<void> {
    return this.request('PUT', '/v1/online-capabilities', snapshot).then(() => undefined)
  }

  updateEmailCapability(snapshot: EmailCapabilitySnapshot): Promise<void> {
    return this.request('PUT', '/v1/email/online-capability', snapshot).then(() => undefined)
  }

  async getEmailPreference(subscriptionId: string, subject: string): Promise<EmailPreferenceWire> {
    const value = await this.json(
      'GET',
      `/v1/subscriptions/${encodeURIComponent(subscriptionId)}/email-preference?subject=${encodeURIComponent(subject)}`
    )
    return validateEmailPreference(value)
  }

  async setEmailPreference(
    subscriptionId: string,
    subject: string,
    enabled: boolean
  ): Promise<EmailPreferenceWire> {
    const value = await this.json(
      'PUT',
      `/v1/subscriptions/${encodeURIComponent(subscriptionId)}/email-preference`,
      { schema_version: 1, subject, enabled }
    )
    return validateEmailPreference(value)
  }

  async createManualEmail(actionId: string, sourceId: string): Promise<EmailOutboxStatusWire> {
    const value = await this.json('POST', '/v1/email-outbox/manual', {
      schema_version: 1,
      action_id: actionId,
      source_id: sourceId
    })
    return validateEmailOutboxStatus(value)
  }

  async listEmailOutbox(): Promise<EmailOutboxStatusWire[]> {
    const value = await this.json<unknown>('GET', '/v1/email-outbox')
    if (!Array.isArray(value) || value.length > 10_000) throw new Error('invalid email outbox list')
    return value.map(validateEmailOutboxStatus)
  }

  async claimEmailDelivery(): Promise<EmailClaim | null> {
    const response = await this.request('POST', '/v1/email-outbox/claim')
    if (response.status === 204) return null
    return validateEmailClaim(await readBoundedJson(response, 3 * 1024 * 1024 + 4096))
  }

  async completeEmailDelivery(body: {
    schema_version: 1
    client_delivery_id: string
    claim_generation: number
    delivery_id: string
    delivered_at: string
  }): Promise<EmailOutboxStatusWire> {
    return validateEmailOutboxStatus(await this.json('POST', '/v1/email-outbox/complete', body))
  }

  async releaseEmailDelivery(body: {
    schema_version: 1
    client_delivery_id: string
    claim_generation: number
    error_code: EmailDeliveryErrorCode
  }): Promise<EmailOutboxStatusWire> {
    return validateEmailOutboxStatus(await this.json('POST', '/v1/email-outbox/release', body))
  }

  listSubscriptions(): Promise<EngineSubscriptionRecord[]> {
    return this.json('GET', '/v1/subscriptions')
  }

  createSubscription(feedUrl: string): Promise<EngineSubscriptionRecord> {
    return this.json('POST', '/v1/subscriptions', { feed_url: feedUrl })
  }

  pollSubscription(subscriptionId: string): Promise<EngineSubscriptionPollResult> {
    return this.json('POST', `/v1/subscriptions/${encodeURIComponent(subscriptionId)}/poll`)
  }

  async deleteSubscription(subscriptionId: string): Promise<void> {
    await this.request('DELETE', `/v1/subscriptions/${encodeURIComponent(subscriptionId)}`)
  }

  // engine/app.py:517 (DELETE /v1/cookies/{domain} — 404 when absent)
  async deleteCookieJar(domain: string): Promise<void> {
    await this.request('DELETE', `/v1/cookies/${encodeURIComponent(domain)}`)
  }

  // engine/app.py:244 (GET /v1/events — SSE; consumed by EventStream below)
  openEvents(signal?: AbortSignal): Promise<Response> {
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

async function readBoundedJson(response: Response, maximumBytes: number): Promise<unknown> {
  if (response.body === null) throw new Error('empty engine response')
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    if (size + value.byteLength > maximumBytes) {
      await reader.cancel()
      throw new Error('engine response too large')
    }
    size += value.byteLength
    chunks.push(value)
  }
  const body = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)) as unknown
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

export interface EventStreamHandlers {
  onEvent(event: PipelineEvent): void
  /**
   * Called with the full job list after EVERY (re)connect: job records are
   * the source of truth, the stream is an optimization (design decision 4).
   */
  onHydrate(jobs: JobRecord[]): void
  onConnectionChange?(connected: boolean): void
}

const DEFAULT_BACKOFF_MS = [500, 1000, 2000, 5000, 10_000]

/**
 * Main-process consumer of `GET /v1/events`: header-authenticated
 * fetch + ReadableStream (no EventSource, no token-in-query), reconnecting
 * with capped backoff, re-hydrating from `GET /v1/jobs` on every (re)connect.
 */
export class EventStream {
  private aborted = false
  private controller: AbortController | null = null
  private readonly backoffMs: readonly number[]
  private readonly sleep: (ms: number) => Promise<void>

  constructor(
    private readonly client: EngineClient,
    private readonly handlers: EventStreamHandlers,
    opts: { backoffMs?: readonly number[]; sleep?: (ms: number) => Promise<void> } = {}
  ) {
    this.backoffMs = opts.backoffMs ?? DEFAULT_BACKOFF_MS
    this.sleep = opts.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)))
  }

  /** Abort the stream — called first in the quit sequence (per P1). */
  abort(): void {
    this.aborted = true
    this.controller?.abort()
  }

  /** Connect/hydrate/read until the stream drops; reconnect with backoff; stop on abort. */
  async run(): Promise<void> {
    let backoffIndex = 0
    while (!this.aborted) {
      try {
        await this.runOnce()
        backoffIndex = 0 // the connection succeeded; restart the ladder
      } catch {
        // fall through to backoff
      }
      if (this.aborted) return
      const delay = this.backoffMs[Math.min(backoffIndex, this.backoffMs.length - 1)] ?? 0
      backoffIndex += 1
      await this.sleep(delay)
    }
  }

  /** One connection lifecycle: open SSE, hydrate, forward events until the stream ends. */
  async runOnce(): Promise<void> {
    this.controller = new AbortController()
    const res = await this.client.openEvents(this.controller.signal)
    this.handlers.onConnectionChange?.(true)
    try {
      this.handlers.onHydrate(await this.client.listJobs())
      await this.forward(res)
    } finally {
      this.handlers.onConnectionChange?.(false)
      this.controller = null
    }
  }

  private async forward(res: Response): Promise<void> {
    if (res.body === null) return
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    const parser = new SseParser()
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) return
        for (const payload of parser.push(decoder.decode(value, { stream: true }))) {
          this.dispatch(payload)
        }
        if (this.aborted) return
      }
    } catch (err) {
      if (this.aborted) return
      throw err
    }
  }

  private dispatch(payload: string): void {
    let event: PipelineEvent
    try {
      event = JSON.parse(payload) as PipelineEvent
    } catch {
      return // a malformed frame is dropped; hydration covers any gap
    }
    this.handlers.onEvent(event)
  }
}
