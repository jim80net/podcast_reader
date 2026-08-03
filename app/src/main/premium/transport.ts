import { PremiumOrigin } from './origin'

export interface DeviceStart { device_code: string; user_code: string; verification_uri: string; expires_in: number; interval: number }
export interface TokenPair { access_token: string; token_type: 'Bearer'; expires_in: number; refresh_token: string }
type Fetch = typeof fetch

export class PremiumRequestError extends Error {
  constructor(readonly status: number, readonly code: string) { super(`premium request failed (${status})`) }
}

export class PremiumTransport {
  constructor(private readonly origin: PremiumOrigin, private readonly fetchFn: Fetch = fetch) {}

  async startDeviceAuthorization(): Promise<DeviceStart> { return validateDeviceStart(await this.request('/v1/device-authorizations', { method: 'POST', body: JSON.stringify({ client: 'desktop' }) })) }
  async pollDeviceToken(deviceCode: string): Promise<TokenPair> { return validateTokenPair(await this.request('/v1/device-authorizations/token', { method: 'POST', body: JSON.stringify({ device_code: deviceCode }) })) }
  async refresh(refreshToken: string): Promise<TokenPair> { return validateTokenPair(await this.request('/v1/tokens/refresh', { method: 'POST', body: JSON.stringify({ refresh_token: refreshToken }) })) }
  entitlement(accessToken: string): Promise<unknown> { return this.request('/v1/me/entitlements', { headers: { Authorization: `Bearer ${accessToken}` } }) }
  inventory(slot: 'library' | 'reader', accessToken: string): Promise<unknown | null> {
    return this.requestInventory(`/v1/ads/inventory/${slot}`, accessToken)
  }
  ownsExternalUrl(url: string): boolean { return this.origin.owns(url) }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const response = await this.fetchFn(this.origin.resolve(path), {
      ...init,
      redirect: 'error',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...init.headers },
      referrerPolicy: 'no-referrer',
      credentials: 'omit'
      , signal: AbortSignal.timeout(15_000)
    })
    const text = await readBoundedBody(response)
    if (!response.ok) {
      let code = 'request_failed'
      try { const parsed = JSON.parse(text) as { code?: unknown }; if (typeof parsed.code === 'string') code = parsed.code } catch { /* bounded generic error */ }
      throw new PremiumRequestError(response.status, code)
    }
    return JSON.parse(text) as T
  }

  private async requestInventory(path: string, accessToken: string): Promise<unknown | null> {
    const response = await this.fetchFn(this.origin.resolve(path), {
      redirect: 'error',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` },
      referrerPolicy: 'no-referrer',
      credentials: 'omit',
      signal: AbortSignal.timeout(15_000)
    })
    const text = await readBoundedBody(response)
    if (response.status === 204) {
      if (text !== '') throw new Error('invalid premium response')
      return null
    }
    if (!response.ok) {
      let code = 'request_failed'
      try { const parsed = JSON.parse(text) as { code?: unknown }; if (typeof parsed.code === 'string') code = parsed.code } catch { /* bounded generic error */ }
      throw new PremiumRequestError(response.status, code)
    }
    return JSON.parse(text) as unknown
  }
}

function validateDeviceStart(value: unknown): DeviceStart {
  const item = value as Partial<DeviceStart>
  if (!hasExactKeys(value, ['device_code', 'expires_in', 'interval', 'user_code', 'verification_uri']) || typeof item.device_code !== 'string' || item.device_code.length < 20 || item.device_code.length > 256 || typeof item.user_code !== 'string' || !/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(item.user_code) || typeof item.verification_uri !== 'string' || !boundedInteger(item.expires_in, 1, 900) || !boundedInteger(item.interval, 1, 60)) throw new Error('invalid device authorization')
  return item as DeviceStart
}

function validateTokenPair(value: unknown): TokenPair {
  const item = value as Partial<TokenPair>
  if (!hasExactKeys(value, ['access_token', 'expires_in', 'refresh_token', 'token_type']) || typeof item.access_token !== 'string' || item.access_token.length < 20 || item.access_token.length > 256 || /\s/.test(item.access_token) || typeof item.refresh_token !== 'string' || item.refresh_token.length < 20 || item.refresh_token.length > 256 || /\s/.test(item.refresh_token) || item.token_type !== 'Bearer' || !boundedInteger(item.expires_in, 1, 3600)) throw new Error('invalid token response')
  return item as TokenPair
}

function boundedInteger(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum
}

function hasExactKeys(value: unknown, expected: string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value).sort().join(',') === [...expected].sort().join(',')
}

async function readBoundedBody(response: Response): Promise<string> {
  const declared = Number(response.headers.get('content-length'))
  if (Number.isFinite(declared) && declared > 64 * 1024) throw new Error('premium response too large')
  if (response.body === null) return ''
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    size += value.byteLength
    if (size > 64 * 1024) { await reader.cancel(); throw new Error('premium response too large') }
    chunks.push(value)
  }
  const body = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength }
  return new TextDecoder('utf-8', { fatal: true }).decode(body)
}
