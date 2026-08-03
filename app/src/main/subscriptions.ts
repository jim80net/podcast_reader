import { EngineRequestError } from './engine-client'
import type { EngineSubscriptionPollResult, EngineSubscriptionRecord } from './engine-client'
import type { SubscriptionPollSummary, SubscriptionSummary } from '../shared/ipc'

const SUBSCRIPTION_ID = /^sub_[0-9a-f]{32}$/
const ERROR_CODE = /^[a-z0-9_]{1,64}$/

export function validateFeedUrlInput(value: unknown): string {
  if (typeof value !== 'string' || value.length < 1 || value.length > 2048) throw new Error('invalid_feed_url')
  return value
}

export function validateSubscriptionId(value: unknown): string {
  if (typeof value !== 'string' || !SUBSCRIPTION_ID.test(value)) throw new Error('invalid_subscription_id')
  return value
}

export function publicSubscription(value: EngineSubscriptionRecord): SubscriptionSummary {
  return {
    id: validateSubscriptionId(value.id),
    title: typeof value.title === 'string' && value.title.length <= 4096 ? value.title : null,
    origin: typeof value.normalized_origin === 'string' && value.normalized_origin.length <= 2048
      ? value.normalized_origin
      : 'unknown',
    lastCheckedAt: safeTime(value.last_checked_at),
    nextCheckAt: safeTime(value.next_check_at),
    lastErrorCode: typeof value.last_error === 'string' && ERROR_CODE.test(value.last_error)
      ? value.last_error
      : value.last_error === null ? null : 'subscription_error'
  }
}

export function publicPollResult(value: EngineSubscriptionPollResult): SubscriptionPollSummary {
  return {
    subscription: publicSubscription(value.subscription),
    discoveredCount: Number.isSafeInteger(value.discovered_count) && value.discovered_count >= 0
      ? value.discovered_count
      : 0,
    notModified: value.not_modified === true
  }
}

export function subscriptionError(error: unknown): Error {
  if (error instanceof EngineRequestError) {
    if (error.status === 404) return new Error('subscription_not_found')
    if (error.status === 409) return new Error('premium_feature_unavailable')
    if (error.status === 400) return new Error('invalid_feed')
  }
  return new Error('subscription_request_failed')
}

function safeTime(value: string | null): string | null {
  if (value === null) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) && value.length <= 64 ? value : null
}
