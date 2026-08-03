import type {
  PremiumAdInventory,
  PremiumAdSlot,
  PremiumProductState
} from '../../shared/ipc'
import type { ProductState } from './contracts'
import { isExactHttpsExternalUrl } from '../external-links'
import type { PremiumDeviceFlow } from './device-flow'
import type { PremiumRuntime } from './runtime'
import { PremiumRequestError } from './transport'
import type { PremiumTransport } from './transport'

interface CachedInventory extends PremiumAdInventory { generation: number }
interface PremiumCapabilitySnapshot {
  schema_version: 1
  subject: string
  entitlement_revision: number
  flags_revision: number
  podcast_subscriptions: boolean
  expires_at: string
}
/** Premium domain's exact view of the frozen engine v1 email capability fixture. */
interface EmailCapabilitySnapshot {
  schema_version: 1
  subject: string
  entitlement_revision: number
  flags_revision: number
  transcript_email: boolean
  expires_at: string
}
interface EmailSenderAccess {
  enable(subject: string): void
  disable(): void
  wake(): void
}
const MAX_TIMER_MS = 2_147_483_647

export interface PremiumControllerDeps {
  runtime: PremiumRuntime
  transport: PremiumTransport
  deviceFlow: PremiumDeviceFlow
  openExternal(url: string): Promise<void>
  invalidated(): void
  stateChanged(state: PremiumProductState): void
  syncCapability?(snapshot: PremiumCapabilitySnapshot): Promise<void>
  syncEmailCapability?(snapshot: EmailCapabilitySnapshot): Promise<void>
  emailSender?: EmailSenderAccess
  capabilitySyncFailed?(): void
  now(): number
  schedule(callback: () => void, milliseconds: number): ReturnType<typeof setTimeout>
  cancel(timer: ReturnType<typeof setTimeout>): void
}

export interface PremiumAccess {
  state(): PremiumProductState
  restore(): Promise<PremiumProductState>
  connect(): Promise<PremiumProductState>
  signOut(): PremiumProductState
  background(): PremiumProductState
  subscriptionsEnabled(): boolean
  emailEnabled(): boolean
  emailSubject(): string | null
  wakeEmailSender(): void
  emailUnavailable(): void
  synchronizeCapability(): Promise<void>
  inventory(slot: PremiumAdSlot): Promise<PremiumAdInventory | null>
  openCta(slot: PremiumAdSlot, url: string): Promise<void>
}

/**
 * Owns presentation-only ad state in the Electron main process. Bearers and
 * backend inventory revisions never cross IPC; every eviction collapses all
 * renderer slots before any refresh attempt.
 */
export class PremiumController implements PremiumAccess {
  private generation = 0
  private readonly cache = new Map<PremiumAdSlot, CachedInventory>()
  private readonly pending = new Map<PremiumAdSlot, Promise<PremiumAdInventory | null>>()
  private expiryTimer: ReturnType<typeof setTimeout> | null = null
  private refreshPromise: Promise<PremiumProductState> | null = null
  private lastCapability: PremiumCapabilitySnapshot | null = null
  private lastEmailCapability: EmailCapabilitySnapshot | null = null
  private capabilityQueue: Promise<void> = Promise.resolve()

  constructor(private readonly deps: PremiumControllerDeps) {}

  state(): PremiumProductState {
    if (this.isEntitlementExpired()) this.failClosed()
    return productState(this.deps.runtime.state)
  }

  async restore(): Promise<PremiumProductState> {
    return this.refresh(true)
  }

  private async refresh(notify: boolean): Promise<PremiumProductState> {
    let operation = this.refreshPromise
    if (operation === null) {
      operation = this.performRefresh()
      this.refreshPromise = operation
    }
    try {
      const state = await operation
      if (notify) this.deps.stateChanged(state)
      return state
    } finally {
      if (this.refreshPromise === operation) this.refreshPromise = null
    }
  }

  private async performRefresh(): Promise<PremiumProductState> {
    this.disableCapabilities(this.deps.runtime.state)
    this.evict()
    await this.deps.runtime.restore()
    this.armEntitlementExpiry()
    this.queueCapabilities(this.deps.runtime.state)
    return this.state()
  }

  async connect(): Promise<PremiumProductState> {
    this.disableCapabilities(this.deps.runtime.state)
    this.evict()
    const generation = this.generation
    const tokens = await this.deps.deviceFlow.authorize()
    if (generation !== this.generation) return this.state()
    await this.deps.runtime.acceptTokens(tokens)
    this.armEntitlementExpiry()
    this.queueCapabilities(this.deps.runtime.state)
    const state = this.state()
    this.deps.stateChanged(state)
    return state
  }

  signOut(): PremiumProductState {
    this.disableCapabilities(this.deps.runtime.state)
    this.evict()
    this.deps.runtime.signOut()
    const state = this.state()
    this.deps.stateChanged(state)
    return state
  }

  background(): PremiumProductState {
    this.disableCapabilities(this.deps.runtime.state)
    this.evict()
    this.deps.runtime.background()
    const state = this.state()
    this.deps.stateChanged(state)
    return state
  }

  subscriptionsEnabled(): boolean {
    const state = this.deps.runtime.state
    return state.state === 'online-premium' && state.podcastSubscriptions && state.refreshAfter > this.deps.now()
  }

  emailEnabled(): boolean {
    const state = this.deps.runtime.state
    return state.state === 'online-premium' && state.transcriptEmail && state.refreshAfter > this.deps.now()
  }

  emailSubject(): string | null {
    const state = this.deps.runtime.state
    if ((state.state === 'online-free' || state.state === 'online-premium') && state.refreshAfter > this.deps.now()) {
      return state.subject
    }
    return this.lastEmailCapability?.subject ?? null
  }

  wakeEmailSender(): void {
    if (this.emailEnabled()) this.deps.emailSender?.wake()
  }

  emailUnavailable(): void {
    if (this.emailEnabled()) this.failClosed()
    else this.deps.emailSender?.disable()
  }

  synchronizeCapability(): Promise<void> {
    this.queueCapabilities(this.deps.runtime.state)
    return this.capabilityQueue
  }

  async inventory(slot: PremiumAdSlot): Promise<PremiumAdInventory | null> {
    if (slot !== 'library' && slot !== 'reader') return null
    const cached = this.cache.get(slot)
    if (cached !== undefined && cached.generation === this.generation && cached.expiresAt > this.deps.now()) {
      return publicInventory(cached)
    }
    const existing = this.pending.get(slot)
    if (existing !== undefined) return existing
    const request = this.loadInventory(slot)
    this.pending.set(slot, request)
    try { return await request }
    finally { if (this.pending.get(slot) === request) this.pending.delete(slot) }
  }

  async openCta(slot: PremiumAdSlot, url: string): Promise<void> {
    const cached = this.cache.get(slot)
    if (
      cached === undefined ||
      cached.generation !== this.generation ||
      cached.expiresAt <= this.deps.now() ||
      cached.creative.ctaUrl !== url ||
      validateHttpsUrl(url) === null
    ) throw new Error('ad destination is unavailable')
    await this.deps.openExternal(url)
  }

  private async loadInventory(slot: PremiumAdSlot): Promise<PremiumAdInventory | null> {
    let state = this.deps.runtime.state
    if (state.state === 'online-unavailable') {
      await this.refresh(false)
      state = this.deps.runtime.state
    }
    if (!eligible(state, this.deps.now())) return null
    const token = this.deps.runtime.bearer
    if (token === null) return null
    let requestGeneration = this.generation
    let value: unknown | null
    try {
      value = await this.deps.transport.inventory(slot, token)
    } catch (error) {
      if (error instanceof PremiumRequestError && error.status === 401) {
        await this.refresh(false)
        state = this.deps.runtime.state
        const refreshed = this.deps.runtime.bearer
        if (!eligible(state, this.deps.now()) || refreshed === null) return null
        requestGeneration = this.generation
        try { value = await this.deps.transport.inventory(slot, refreshed) }
        catch { this.failClosed(); return null }
      } else {
        this.failClosed()
        return null
      }
    }
    if (value === null) return null
    state = this.deps.runtime.state
    if (
      requestGeneration !== this.generation ||
      !eligible(state, this.deps.now())
    ) return null
    try {
      const inventory = validateInventory(value, slot, state.refreshAfter, this.deps.now())
      const cached: CachedInventory = { ...inventory, generation: this.generation }
      this.cache.set(slot, cached)
      this.armInventoryExpiry()
      return publicInventory(cached)
    } catch {
      this.failClosed()
      return null
    }
  }

  private isEntitlementExpired(): boolean {
    const state = this.deps.runtime.state
    return (state.state === 'online-free' || state.state === 'online-premium') && state.refreshAfter <= this.deps.now()
  }

  private failClosed(): void {
    this.disableCapabilities(this.deps.runtime.state)
    this.evict()
    this.deps.runtime.background()
    this.deps.stateChanged(productState(this.deps.runtime.state))
  }

  private evict(): void {
    this.generation += 1
    this.cache.clear()
    if (this.expiryTimer !== null) this.deps.cancel(this.expiryTimer)
    this.expiryTimer = null
    this.deps.invalidated()
  }

  private armEntitlementExpiry(): void {
    const state = this.deps.runtime.state
    if (state.state !== 'online-free' && state.state !== 'online-premium') return
    this.armExpiry(state.refreshAfter)
  }

  private armInventoryExpiry(): void {
    const expiries = [...this.cache.values()].map((item) => item.expiresAt)
    const state = this.deps.runtime.state
    if (state.state === 'online-free' || state.state === 'online-premium') expiries.push(state.refreshAfter)
    if (expiries.length > 0) this.armExpiry(Math.min(...expiries))
  }

  private armExpiry(expiresAt: number): void {
    if (this.expiryTimer !== null) this.deps.cancel(this.expiryTimer)
    const generation = this.generation
    const delay = Math.max(0, expiresAt - this.deps.now())
    this.expiryTimer = this.deps.schedule(() => {
      if (generation !== this.generation) return
      if (expiresAt > this.deps.now()) { this.armExpiry(expiresAt); return }
      this.expire()
    }, Math.min(delay, MAX_TIMER_MS))
  }

  private expire(): void {
    const state = this.deps.runtime.state
    this.disableCapabilities(state)
    this.evict()
    if ((state.state === 'online-free' || state.state === 'online-premium') && state.refreshAfter <= this.deps.now()) {
      this.deps.runtime.background()
      this.deps.stateChanged(productState(this.deps.runtime.state))
      void this.refresh(true).catch(() => this.failClosed())
      return
    }
    this.deps.stateChanged(productState(state))
  }

  private disableCapabilities(state: ProductState): void {
    this.deps.emailSender?.disable()
    const subscription = subscriptionCapabilitySnapshot(state, false) ?? this.lastCapability
    const email = emailCapabilitySnapshot(state, false) ?? this.lastEmailCapability
    if (subscription !== null) this.lastCapability = { ...subscription, podcast_subscriptions: false }
    if (email !== null) this.lastEmailCapability = { ...email, transcript_email: false }
    this.enqueueCapabilities(this.lastCapability, this.lastEmailCapability)
  }

  private queueCapabilities(state: ProductState): void {
    if (state.state !== 'online-free' && state.state !== 'online-premium') return
    const subscription = subscriptionCapabilitySnapshot(state, this.subscriptionsEnabled())
    const email = emailCapabilitySnapshot(state, this.emailEnabled())
    this.deps.emailSender?.disable()
    this.lastCapability = subscription
    this.lastEmailCapability = email
    this.enqueueCapabilities(
      subscription,
      email,
      subscription.podcast_subscriptions || email.transcript_email
    )
  }

  private enqueueCapabilities(
    subscription: PremiumCapabilitySnapshot | null,
    email: EmailCapabilitySnapshot | null,
    disableFirst = false
  ): void {
    if (this.deps.syncCapability === undefined && this.deps.syncEmailCapability === undefined) return
    const syncSubscription = this.deps.syncCapability
    const syncEmail = this.deps.syncEmailCapability
    this.capabilityQueue = this.capabilityQueue
      .catch(() => undefined)
      .then(async () => {
        if (disableFirst) {
          if (email !== null && syncEmail !== undefined) await syncEmail({ ...email, transcript_email: false })
          if (subscription !== null && syncSubscription !== undefined) await syncSubscription({ ...subscription, podcast_subscriptions: false })
        }
        if (email !== null && !email.transcript_email && syncEmail !== undefined) await syncEmail(email)
        if (subscription !== null && syncSubscription !== undefined) await syncSubscription(subscription)
        if (email !== null && email.transcript_email && syncEmail !== undefined) await syncEmail(email)
        if (email?.transcript_email === true && syncEmail !== undefined && this.emailEnabled() && this.emailSubject() === email.subject) {
          this.deps.emailSender?.enable(email.subject)
        }
      })
      .catch(() => {
        this.deps.emailSender?.disable()
        this.deps.capabilitySyncFailed?.()
      })
  }
}

export const disabledPremiumAccess = (): PremiumAccess => ({
  state: () => ({ state: 'local', available: false }),
  restore: async () => ({ state: 'local', available: false }),
  connect: async () => { throw new Error('premium account service is not configured') },
  signOut: () => ({ state: 'local', available: false }),
  background: () => ({ state: 'local', available: false }),
  subscriptionsEnabled: () => false,
  emailEnabled: () => false,
  emailSubject: () => null,
  wakeEmailSender: () => undefined,
  emailUnavailable: () => undefined,
  synchronizeCapability: async () => undefined,
  inventory: async () => null,
  openCta: async () => { throw new Error('ad destination is unavailable') }
})

function productState(state: ProductState): PremiumProductState {
  switch (state.state) {
    case 'local': return { state: 'local', available: true }
    case 'online-unavailable': return { state: 'online-unavailable', available: true }
    case 'online-free': return { state: 'online-free', available: true, expiresAt: state.refreshAfter }
    case 'online-premium': return { state: 'online-premium', available: true, expiresAt: state.refreshAfter, subscriptionsAvailable: state.podcastSubscriptions, emailAvailable: state.transcriptEmail }
  }
}

type OnlineProductState = Extract<ProductState, { state: 'online-free' | 'online-premium' }>

function subscriptionCapabilitySnapshot(state: OnlineProductState, enabled: boolean): PremiumCapabilitySnapshot
function subscriptionCapabilitySnapshot(state: ProductState, enabled: boolean): PremiumCapabilitySnapshot | null
function subscriptionCapabilitySnapshot(state: ProductState, enabled: boolean): PremiumCapabilitySnapshot | null {
  if (state.state !== 'online-free' && state.state !== 'online-premium') return null
  return {
    schema_version: 1,
    subject: state.subject,
    entitlement_revision: state.entitlementRevision,
    flags_revision: state.flagsRevision,
    podcast_subscriptions: enabled,
    expires_at: new Date(state.refreshAfter).toISOString().replace('.000Z', 'Z')
  }
}

function emailCapabilitySnapshot(state: OnlineProductState, enabled: boolean): EmailCapabilitySnapshot
function emailCapabilitySnapshot(state: ProductState, enabled: boolean): EmailCapabilitySnapshot | null
function emailCapabilitySnapshot(state: ProductState, enabled: boolean): EmailCapabilitySnapshot | null {
  if (state.state !== 'online-free' && state.state !== 'online-premium') return null
  return {
    schema_version: 1,
    subject: state.subject,
    entitlement_revision: state.entitlementRevision,
    flags_revision: state.flagsRevision,
    transcript_email: enabled,
    expires_at: new Date(state.refreshAfter).toISOString().replace('.000Z', 'Z')
  }
}

function eligible(state: ProductState, now: number): state is Extract<ProductState, { state: 'online-free' }> {
  return state.state === 'online-free' && state.adPolicy === 'house' && state.refreshAfter > now
}

function publicInventory(value: CachedInventory): PremiumAdInventory {
  return { slot: value.slot, expiresAt: value.expiresAt, creative: { ...value.creative } }
}

function validateInventory(value: unknown, slot: PremiumAdSlot, refreshAfter: number, now: number): PremiumAdInventory {
  const root = record(value)
  if (root.schema_version !== 1 || root.slot !== slot || !boundedInteger(root.inventory_revision, 0, Number.MAX_SAFE_INTEGER) || !Array.isArray(root.items) || root.items.length < 1 || root.items.length > 10) throw new Error('invalid ad inventory')
  const expiresAt = parseCanonicalTime(root.expires_at)
  if (expiresAt <= now || expiresAt > refreshAfter) throw new Error('stale ad inventory')
  const items = root.items.map((raw) => {
    const item = record(raw)
    if (!boundedString(item.id, 1, 128) || !boundedInteger(item.revision, 0, Number.MAX_SAFE_INTEGER) || item.kind !== 'text' || !boundedString(item.title, 1, 120) || !boundedString(item.body, 1, 500)) throw new Error('invalid ad creative')
    const ctaUrl = validateHttpsUrl(item.cta_url)
    if (ctaUrl === null) throw new Error('invalid ad destination')
    return { title: item.title, body: item.body, ctaUrl }
  })
  const creative = items[0]
  if (creative === undefined) throw new Error('invalid ad inventory')
  return { slot, expiresAt, creative }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('invalid ad inventory')
  return value as Record<string, unknown>
}

function boundedString(value: unknown, minimum: number, maximum: number): value is string {
  return typeof value === 'string' && Array.from(value).length >= minimum && Array.from(value).length <= maximum
}

function boundedInteger(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum
}

function parseCanonicalTime(value: unknown): number {
  if (typeof value !== 'string') throw new Error('invalid ad expiry')
  const parsed = Date.parse(value)
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString().replace('.000Z', 'Z') !== value) throw new Error('invalid ad expiry')
  return parsed
}

function validateHttpsUrl(value: unknown): string | null {
  return typeof value === 'string' && isExactHttpsExternalUrl(value) ? value : null
}
