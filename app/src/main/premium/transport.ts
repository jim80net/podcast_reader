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
    const text = await response.text()
    if (text.length > 64 * 1024) throw new Error('premium response too large')
    if (!response.ok) {
      let code = 'request_failed'
      try { const parsed = JSON.parse(text) as { code?: unknown }; if (typeof parsed.code === 'string') code = parsed.code } catch { /* bounded generic error */ }
      throw new PremiumRequestError(response.status, code)
    }
    return JSON.parse(text) as T
  }
}

function validateDeviceStart(value: unknown): DeviceStart {
  const item = value as Partial<DeviceStart>
  if (typeof item.device_code !== 'string' || item.device_code.length < 20 || typeof item.user_code !== 'string' || typeof item.verification_uri !== 'string' || !Number.isInteger(item.expires_in) || !Number.isInteger(item.interval)) throw new Error('invalid device authorization')
  return item as DeviceStart
}

function validateTokenPair(value: unknown): TokenPair {
  const item = value as Partial<TokenPair>
  if (typeof item.access_token !== 'string' || item.access_token.length < 20 || typeof item.refresh_token !== 'string' || item.refresh_token.length < 20 || item.token_type !== 'Bearer' || !Number.isInteger(item.expires_in)) throw new Error('invalid token response')
  return item as TokenPair
}
