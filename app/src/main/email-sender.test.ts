import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { EmailSender } from './email-sender'
import { PremiumOrigin } from './premium/origin'
import { PremiumRequestError, PremiumTransport } from './premium/transport'
import type { EngineClient } from './engine-client'
import type { EmailClaim, EmailDeliveryRequest } from './email-contracts'

const engineFixture = <T>(name: string): T => JSON.parse(readFileSync(
  join(process.cwd(), '..', 'src', 'podcast_reader', 'engine', 'contracts', 'v1', 'email', name),
  'utf8'
)) as T

const relayFixture = <T>(name: string): T => JSON.parse(readFileSync(
  join(process.cwd(), '..', 'services', 'premium', 'contracts', 'v1', 'email', name),
  'utf8'
)) as T

const settle = async (): Promise<void> => {
  await vi.waitFor(() => undefined)
  await Promise.resolve()
}

describe('main-only transcript email sender', () => {
  it('moves the exact frozen claim to the relay without claim metadata and completes locally', async () => {
    const claim = engineFixture<EmailClaim>('claim.json')
    const delivered = relayFixture<Awaited<ReturnType<PremiumTransport['deliverEmail']>>>('delivered.json')
    const engine = {
      claimEmailDelivery: vi.fn().mockResolvedValueOnce(claim).mockResolvedValue(null),
      completeEmailDelivery: vi.fn(async () => ({})),
      releaseEmailDelivery: vi.fn(async () => ({}))
    }
    const transport = {
      deliverEmail: vi.fn(async (_request: EmailDeliveryRequest, _bearer: string) => delivered)
    }
    const sender = new EmailSender({
      engine: () => engine as unknown as EngineClient,
      authorization: () => ({ subject: 'usr_contract_fixture_01', bearer: 'access_secret' }),
      transport: transport as unknown as Pick<PremiumTransport, 'deliverEmail'>,
      unavailable: vi.fn(),
      schedule: () => 1 as unknown as ReturnType<typeof setTimeout>,
      cancel: vi.fn()
    })

    sender.enable('usr_contract_fixture_01')
    await vi.waitFor(() => expect(engine.completeEmailDelivery).toHaveBeenCalledOnce())

    const expected = relayFixture<EmailDeliveryRequest>('request-subscription.json')
    expect(transport.deliverEmail).toHaveBeenCalledWith(expected, 'access_secret')
    expect(transport.deliverEmail.mock.calls[0]?.[0]).not.toHaveProperty('claim_generation')
    expect(engine.completeEmailDelivery).toHaveBeenCalledWith(engineFixture('completion.json'))
    expect(engine.releaseEmailDelivery).not.toHaveBeenCalled()
  })

  it('rechecks subject authorization after claim and refunds a paused attempt before upload', async () => {
    let resolveClaim!: (claim: EmailClaim) => void
    const pendingClaim = new Promise<EmailClaim>((resolve) => { resolveClaim = resolve })
    const engine = {
      claimEmailDelivery: vi.fn(() => pendingClaim),
      completeEmailDelivery: vi.fn(),
      releaseEmailDelivery: vi.fn(async () => ({}))
    }
    const transport = { deliverEmail: vi.fn() }
    const sender = new EmailSender({
      engine: () => engine as unknown as EngineClient,
      authorization: () => ({ subject: 'usr_contract_fixture_01', bearer: 'access_secret' }),
      transport: transport as unknown as Pick<PremiumTransport, 'deliverEmail'>,
      unavailable: vi.fn(),
      schedule: () => 1 as unknown as ReturnType<typeof setTimeout>,
      cancel: vi.fn()
    })
    sender.enable('usr_contract_fixture_01')
    await vi.waitFor(() => expect(engine.claimEmailDelivery).toHaveBeenCalledOnce())
    sender.disable()
    resolveClaim(engineFixture('claim.json'))
    await vi.waitFor(() => expect(engine.releaseEmailDelivery).toHaveBeenCalledOnce())
    expect(engine.releaseEmailDelivery).toHaveBeenCalledWith({
      ...engineFixture<Record<string, unknown>>('release.json'),
      error_code: 'premium_feature_unavailable'
    })
    expect(transport.deliverEmail).not.toHaveBeenCalled()
  })

  it('fails closed when the relay independently rejects entitlement', async () => {
    const engine = {
      claimEmailDelivery: vi.fn().mockResolvedValueOnce(engineFixture('claim.json')),
      completeEmailDelivery: vi.fn(),
      releaseEmailDelivery: vi.fn(async () => ({}))
    }
    const unavailable = vi.fn()
    const sender = new EmailSender({
      engine: () => engine as unknown as EngineClient,
      authorization: () => ({ subject: 'usr_contract_fixture_01', bearer: 'access_secret' }),
      transport: {
        deliverEmail: vi.fn(async () => {
          throw new PremiumRequestError(403, 'premium_feature_unavailable')
        })
      } as unknown as Pick<PremiumTransport, 'deliverEmail'>,
      unavailable,
      schedule: () => 1 as unknown as ReturnType<typeof setTimeout>,
      cancel: vi.fn()
    })
    sender.enable('usr_contract_fixture_01')
    await vi.waitFor(() => expect(unavailable).toHaveBeenCalledOnce())
    expect(engine.releaseEmailDelivery).toHaveBeenCalledWith({
      ...engineFixture<Record<string, unknown>>('release.json'),
      error_code: 'premium_feature_unavailable'
    })
    expect(engine.completeEmailDelivery).not.toHaveBeenCalled()
    await settle()
  })
})

describe('premium email relay transport', () => {
  it('posts the backend-owned manual fixture with only the premium bearer', async () => {
    const request = relayFixture<EmailDeliveryRequest>('request-manual.json')
    const delivered = relayFixture<unknown>('delivered.json')
    const fetchFn = vi.fn(async (
      _input: string | URL | Request,
      _init?: RequestInit
    ) => new Response(JSON.stringify(delivered), { status: 200 }))
    const transport = new PremiumTransport(
      PremiumOrigin.fromTrustedConfiguration('https://premium.example'),
      fetchFn as typeof fetch
    )
    await expect(transport.deliverEmail(request, 'premium_access_secret')).resolves.toEqual(delivered)
    expect(fetchFn).toHaveBeenCalledOnce()
    expect(fetchFn.mock.calls[0]?.[0]).toBe('https://premium.example/v1/email-deliveries')
    const init = fetchFn.mock.calls[0]?.[1]
    expect(JSON.parse(String(init?.body))).toEqual(request)
    expect(JSON.parse(String(init?.body))).not.toHaveProperty('recipient')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer premium_access_secret')
  })
})
