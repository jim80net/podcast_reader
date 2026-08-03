import { reduceEntitlement } from './contracts'
import type { ProductState } from './contracts'
import type { PremiumCredentialStore } from './credentials'
import type { PremiumTransport } from './transport'

export class PremiumRuntime {
  private current: ProductState = { state: 'local' }
  private accessToken: string | null = null
  private generation = 0
  constructor(private readonly transport: PremiumTransport, private readonly credentials: PremiumCredentialStore) {}

  get state(): ProductState { return this.current }
  /** Main-process-only bearer for inventory requests; never exposed through IPC. */
  get bearer(): string | null { return this.accessToken }

  async restore(): Promise<ProductState> {
    const generation = ++this.generation
    const stored = this.credentials.get()
    if (stored === null) return this.setLocal()
    let rotated = false
    try {
      const tokens = await this.transport.refresh(stored.refreshToken)
      rotated = true
      if (generation !== this.generation) return this.current
      this.credentials.set({ subject: stored.subject, refreshToken: tokens.refresh_token })
      if (generation !== this.generation) return this.current
      this.accessToken = tokens.access_token
      const entitlement = await this.transport.entitlement(tokens.access_token)
      if (generation !== this.generation) return this.current
      this.current = reduceEntitlement(entitlement, stored.subject)
    } catch {
      if (generation !== this.generation) return this.current
      if (rotated) { try { this.credentials.set(null) } catch { /* stale token must never authorize */ } }
      this.accessToken = null
      this.current = { state: 'online-unavailable' }
    }
    return this.current
  }

  async acceptTokens(tokens: { access_token: string; refresh_token: string }): Promise<ProductState> {
    const generation = ++this.generation
    this.accessToken = tokens.access_token
    try {
      const entitlement = await this.transport.entitlement(tokens.access_token)
      if (generation !== this.generation) return this.current
      const subject = typeof entitlement === 'object' && entitlement !== null ? (entitlement as { subject?: unknown }).subject : null
      if (typeof subject !== 'string' || subject === '') throw new Error('invalid premium contract')
      this.current = reduceEntitlement(entitlement, subject)
      this.credentials.set({ subject, refreshToken: tokens.refresh_token })
    } catch (error) {
      if (generation !== this.generation) return this.current
      try { this.credentials.set(null) } catch { /* in-memory state still fails closed */ }
      this.accessToken = null
      this.current = { state: 'online-unavailable' }
      throw error
    }
    return this.current
  }

  signOut(): ProductState {
    ++this.generation
    this.accessToken = null
    this.setLocal()
    this.credentials.set(null)
    return this.current
  }
  background(): ProductState { ++this.generation; this.accessToken = null; this.current = this.credentials.get() === null ? { state: 'local' } : { state: 'online-unavailable' }; return this.current }
  private setLocal(): ProductState { this.current = { state: 'local' }; return this.current }
}
