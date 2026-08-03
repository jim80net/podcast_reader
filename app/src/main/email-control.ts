import { EngineRequestError } from './engine-client'
import type { EmailOutboxStatusWire, EmailPreferenceWire } from './email-contracts'
import type { EmailDeliverySummary, EmailPreferenceSummary } from '../shared/ipc'

const SOURCE_ID = /^[0-9a-f]{64}$/

export function validateEmailSourceId(value: unknown): string {
  if (typeof value !== 'string' || !SOURCE_ID.test(value)) throw new Error('invalid_source_id')
  return value
}

export function publicEmailPreference(value: EmailPreferenceWire): EmailPreferenceSummary {
  return {
    subscriptionId: value.subscription_id,
    enabled: value.enabled,
    consentRevision: value.consent_revision
  }
}

export function publicEmailStatus(value: EmailOutboxStatusWire): EmailDeliverySummary {
  return {
    clientDeliveryId: value.client_delivery_id,
    subscriptionId: value.subscription_id,
    consentKind: value.consent_kind,
    state: value.state,
    attempts: value.attempts,
    errorCode: value.error_code,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
    deliveredAt: value.delivered_at
  }
}

export function emailRequestError(error: unknown): Error {
  if (error instanceof EngineRequestError) {
    if (error.detail === 'premium_feature_unavailable') return new Error('premium_feature_unavailable')
    if (error.detail === 'idempotency_conflict') return new Error('idempotency_conflict')
    if (error.status === 404) return new Error('email_source_not_found')
    if (error.status === 400 || error.status === 422) return new Error('invalid_email_request')
  }
  return new Error('email_request_failed')
}
