import { PremiumRequestError } from './premium/transport'
import type { EngineClient } from './engine-client'
import type {
  EmailClaim,
  EmailDeliveryErrorCode,
  EmailDeliveryRequest,
  EmailDeliveryResult
} from './email-contracts'
import type { PremiumTransport } from './premium/transport'

const POLL_INTERVAL_MS = 30_000
const MAX_BATCH = 8
const RELAY_ERRORS = new Set<EmailDeliveryErrorCode>([
  'premium_feature_unavailable',
  'delivery_too_large',
  'idempotency_conflict',
  'delivery_unavailable',
  'email_not_verified'
])

interface EmailAuthorization {
  subject: string
  bearer: string
}

export interface EmailSenderDeps {
  engine(): EngineClient | null
  authorization(): EmailAuthorization | null
  transport: Pick<PremiumTransport, 'deliverEmail'>
  unavailable(): void
  schedule(callback: () => void, milliseconds: number): ReturnType<typeof setTimeout>
  cancel(timer: ReturnType<typeof setTimeout>): void
}

/**
 * Main-process-only bridge from the content-bearing local claim to the
 * authenticated relay. Neither bearer nor transcript has an IPC path.
 */
export class EmailSender {
  private subject: string | null = null
  private generation = 0
  private running = false
  private wakeAfterRun = false
  private timer: ReturnType<typeof setTimeout> | null = null

  constructor(private readonly deps: EmailSenderDeps) {}

  enable(subject: string): void {
    if (this.subject === subject) {
      this.wake()
      return
    }
    this.disable()
    this.subject = subject
    this.generation += 1
    this.wake()
  }

  disable(): void {
    this.subject = null
    this.generation += 1
    this.wakeAfterRun = false
    if (this.timer !== null) this.deps.cancel(this.timer)
    this.timer = null
  }

  wake(): void {
    if (this.subject === null) return
    if (this.timer !== null) this.deps.cancel(this.timer)
    this.timer = null
    if (this.running) {
      this.wakeAfterRun = true
      return
    }
    const generation = this.generation
    const subject = this.subject
    this.running = true
    void this.drain(generation, subject)
      .catch(() => undefined)
      .finally(() => {
        this.running = false
        if (this.subject !== subject || this.generation !== generation) return
        const delay = this.wakeAfterRun ? 0 : POLL_INTERVAL_MS
        this.wakeAfterRun = false
        this.timer = this.deps.schedule(() => {
          this.timer = null
          this.wake()
        }, delay)
      })
  }

  private async drain(generation: number, subject: string): Promise<void> {
    for (let index = 0; index < MAX_BATCH; index += 1) {
      const authorization = this.currentAuthorization(generation, subject)
      const engine = this.deps.engine()
      if (authorization === null || engine === null) return
      let claim: EmailClaim | null
      try {
        claim = await engine.claimEmailDelivery()
      } catch {
        return
      }
      if (claim === null) return
      const checked = this.currentAuthorization(generation, subject)
      if (checked === null) {
        await this.release(engine, claim, 'premium_feature_unavailable')
        return
      }
      const request: EmailDeliveryRequest = {
        schema_version: 1,
        client_delivery_id: claim.client_delivery_id,
        consent_kind: claim.consent_kind,
        title: claim.title,
        transcript_text: claim.transcript_text,
        content_sha256: claim.content_sha256
      }
      try {
        const delivered = await this.deps.transport.deliverEmail(request, checked.bearer)
        if (delivered.client_delivery_id !== claim.client_delivery_id) {
          throw new Error('email delivery response mismatch')
        }
        await this.complete(engine, claim, delivered)
      } catch (error) {
        const code = relayError(error)
        await this.release(engine, claim, code)
        if (code === 'premium_feature_unavailable') {
          this.disable()
          this.deps.unavailable()
          return
        }
      }
      if (this.currentAuthorization(generation, subject) === null) return
    }
    this.wakeAfterRun = true
  }

  private currentAuthorization(generation: number, subject: string): EmailAuthorization | null {
    if (generation !== this.generation || this.subject !== subject) return null
    const authorization = this.deps.authorization()
    return authorization?.subject === subject ? authorization : null
  }

  private async complete(
    engine: EngineClient,
    claim: EmailClaim,
    delivered: EmailDeliveryResult
  ): Promise<void> {
    await engine.completeEmailDelivery({
      schema_version: 1,
      client_delivery_id: claim.client_delivery_id,
      claim_generation: claim.claim_generation,
      delivery_id: delivered.delivery_id,
      delivered_at: delivered.delivered_at
    })
  }

  private async release(
    engine: EngineClient,
    claim: EmailClaim,
    errorCode: EmailDeliveryErrorCode
  ): Promise<void> {
    try {
      await engine.releaseEmailDelivery({
        schema_version: 1,
        client_delivery_id: claim.client_delivery_id,
        claim_generation: claim.claim_generation,
        error_code: errorCode
      })
    } catch {
      // The claim lease is the recovery path if the engine disappears here.
    }
  }
}

function relayError(error: unknown): EmailDeliveryErrorCode {
  if (error instanceof PremiumRequestError && RELAY_ERRORS.has(error.code as EmailDeliveryErrorCode)) {
    return error.code as EmailDeliveryErrorCode
  }
  return 'delivery_unavailable'
}
