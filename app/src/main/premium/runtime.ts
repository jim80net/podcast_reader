import { reduceEntitlement } from './contracts'
import type { ProductState } from './contracts'
import type { PremiumCredentialStore } from './credentials'
import type { PremiumTransport } from './transport'

export class PremiumRuntime {
  private current: ProductState = { state: 'local' }
  private accessToken: string | null = null
  constructor(private readonly transport: PremiumTransport, private readonly credentials: PremiumCredentialStore) {}

  get state(): ProductState { return this.current }
  /** Main-process-only bearer for inventory requests; never exposed through IPC. */
  get bearer(): string | null { return this.accessToken }

  async restore(): Promise<ProductState> {
    const stored = this.credentials.get()
    if (stored === null) return this.setLocal()
    try {
      const tokens = await this.transport.refresh(stored.refreshToken)
      this.credentials.set({ subject: stored.subject, refreshToken: tokens.refresh_token })
      this.accessToken = tokens.access_token
      this.current = reduceEntitlement(await this.transport.entitlement(tokens.access_token), stored.subject)
    } catch {
      this.accessToken = null
      this.current = { state: 'online-unavailable' }
    }
    return this.current
  }

  async acceptTokens(tokens: { access_token: string; refresh_token: string }): Promise<ProductState> {
    this.accessToken = tokens.access_token
    try {
      const entitlement = await this.transport.entitlement(tokens.access_token)
      const subject = typeof entitlement === 'object' && entitlement !== null ? (entitlement as { subject?: unknown }).subject : null
      if (typeof subject !== 'string' || subject === '') throw new Error('invalid premium contract')
      this.current = reduceEntitlement(entitlement, subject)
      this.credentials.set({ subject, refreshToken: tokens.refresh_token })
    } catch (error) {
      this.accessToken = null
      this.current = { state: 'online-unavailable' }
      throw error
    }
    return this.current
  }

  signOut(): ProductState { this.credentials.set(null); this.accessToken = null; return this.setLocal() }
  background(): ProductState { this.accessToken = null; this.current = this.credentials.get() === null ? { state: 'local' } : { state: 'online-unavailable' }; return this.current }
  private setLocal(): ProductState { this.current = { state: 'local' }; return this.current }
}
