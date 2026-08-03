import { createHash } from 'node:crypto'

export type EmailConsentKind = 'subscription_completion' | 'manual'
export type EmailOutboxState = 'pending' | 'claimed' | 'delivered' | 'failed' | 'cancelled'
export type EmailDeliveryErrorCode =
  | 'premium_feature_unavailable'
  | 'delivery_too_large'
  | 'idempotency_conflict'
  | 'delivery_unavailable'
  | 'email_not_verified'
  | 'artifact_unavailable'

/** Main's exact view of the frozen engine v1 email capability fixture. */
export interface EmailCapabilitySnapshot {
  schema_version: 1
  subject: string
  entitlement_revision: number
  flags_revision: number
  transcript_email: boolean
  expires_at: string
}

/** Main's exact view of the frozen engine v1 claim fixture. */
export interface EmailClaim {
  schema_version: 1
  client_delivery_id: string
  claim_generation: number
  consent_kind: EmailConsentKind
  title: string
  transcript_text: string
  content_sha256: string
}

export type EmailDeliveryRequest = Omit<EmailClaim, 'claim_generation'>

/** Main's exact view of the frozen relay v1 success fixture. */
export interface EmailDeliveryResult {
  schema_version: 1
  delivery_id: string
  client_delivery_id: string
  state: 'delivered'
  destination: 'dev_maildir'
  delivered_at: string
}

export interface EmailPreferenceWire {
  subscription_id: string
  enabled: boolean
  consent_revision: number
}

export interface EmailOutboxStatusWire {
  client_delivery_id: string
  subscription_id: string | null
  consent_kind: EmailConsentKind
  state: EmailOutboxState
  attempts: number
  error_code: EmailDeliveryErrorCode | null
  created_at: string
  updated_at: string
  delivered_at: string | null
}

const EMAIL_ID = /^eml_[A-Za-z0-9_-]{24}$/
const DELIVERY_ID = /^del_[A-Za-z0-9_-]{24}$/
const SUBSCRIPTION_ID = /^sub_[0-9a-f]{32}$/
const SHA256 = /^[0-9a-f]{64}$/
const EMAIL_CONTENT_MAX_BYTES = 512 * 1024
const EMAIL_CONTENT_MAX_LINES = 20_000

export function validateEmailClaim(value: unknown): EmailClaim {
  const item = exactRecord(value, [
    'claim_generation',
    'client_delivery_id',
    'consent_kind',
    'content_sha256',
    'schema_version',
    'title',
    'transcript_text'
  ])
  if (
    item.schema_version !== 1 ||
    typeof item.client_delivery_id !== 'string' ||
    !EMAIL_ID.test(item.client_delivery_id) ||
    !positiveInteger(item.claim_generation) ||
    (item.consent_kind !== 'subscription_completion' && item.consent_kind !== 'manual') ||
    typeof item.title !== 'string' ||
    Array.from(item.title).length < 1 ||
    Array.from(item.title).length > 200 ||
    item.title.normalize('NFC') !== item.title ||
    hasControl(item.title, false) ||
    typeof item.transcript_text !== 'string' ||
    item.transcript_text.length < 1 ||
    item.transcript_text.normalize('NFC') !== item.transcript_text ||
    hasControl(item.transcript_text, true) ||
    new TextEncoder().encode(item.transcript_text).byteLength > EMAIL_CONTENT_MAX_BYTES ||
    item.transcript_text.split('\n').length > EMAIL_CONTENT_MAX_LINES ||
    typeof item.content_sha256 !== 'string' ||
    !SHA256.test(item.content_sha256) ||
    createHash('sha256').update(item.transcript_text, 'utf8').digest('hex') !== item.content_sha256
  ) throw new Error('invalid email claim')
  return item as unknown as EmailClaim
}

export function validateEmailDelivery(value: unknown): EmailDeliveryResult {
  const item = exactRecord(value, [
    'client_delivery_id',
    'delivered_at',
    'delivery_id',
    'destination',
    'schema_version',
    'state'
  ])
  if (
    item.schema_version !== 1 ||
    typeof item.delivery_id !== 'string' ||
    !DELIVERY_ID.test(item.delivery_id) ||
    typeof item.client_delivery_id !== 'string' ||
    !EMAIL_ID.test(item.client_delivery_id) ||
    item.state !== 'delivered' ||
    item.destination !== 'dev_maildir' ||
    !canonicalTime(item.delivered_at)
  ) throw new Error('invalid email delivery response')
  return item as unknown as EmailDeliveryResult
}

export function validateEmailPreference(value: unknown): EmailPreferenceWire {
  const item = exactRecord(value, ['consent_revision', 'enabled', 'subscription_id'])
  if (
    typeof item.subscription_id !== 'string' ||
    !SUBSCRIPTION_ID.test(item.subscription_id) ||
    typeof item.enabled !== 'boolean' ||
    !nonnegativeInteger(item.consent_revision)
  ) throw new Error('invalid email preference')
  return item as unknown as EmailPreferenceWire
}

export function validateEmailOutboxStatus(value: unknown): EmailOutboxStatusWire {
  const item = exactRecord(value, [
    'attempts',
    'client_delivery_id',
    'consent_kind',
    'created_at',
    'delivered_at',
    'error_code',
    'state',
    'subscription_id',
    'updated_at'
  ])
  const errors: Array<EmailDeliveryErrorCode | null> = [
    null,
    'premium_feature_unavailable',
    'delivery_too_large',
    'idempotency_conflict',
    'delivery_unavailable',
    'email_not_verified',
    'artifact_unavailable'
  ]
  if (
    typeof item.client_delivery_id !== 'string' ||
    !EMAIL_ID.test(item.client_delivery_id) ||
    (item.subscription_id !== null &&
      (typeof item.subscription_id !== 'string' || !SUBSCRIPTION_ID.test(item.subscription_id))) ||
    (item.consent_kind !== 'subscription_completion' && item.consent_kind !== 'manual') ||
    !['pending', 'claimed', 'delivered', 'failed', 'cancelled'].includes(String(item.state)) ||
    !nonnegativeInteger(item.attempts) ||
    !errors.includes(item.error_code as EmailDeliveryErrorCode | null) ||
    !canonicalTime(item.created_at) ||
    !canonicalTime(item.updated_at) ||
    (item.delivered_at !== null && !canonicalTime(item.delivered_at))
  ) throw new Error('invalid email outbox status')
  return item as unknown as EmailOutboxStatusWire
}

function exactRecord(value: unknown, keys: string[]): Record<string, unknown> {
  if (
    typeof value !== 'object' ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(',') !== [...keys].sort().join(',')
  ) throw new Error('invalid email contract')
  return value as Record<string, unknown>
}

function hasControl(value: string, allowNewlines: boolean): boolean {
  for (const character of value) {
    const code = character.codePointAt(0) ?? 0
    if ((code < 32 || (code >= 127 && code <= 159)) && !(allowNewlines && (character === '\n' || character === '\t'))) return true
  }
  return false
}

function canonicalTime(value: unknown): value is string {
  if (typeof value !== 'string' || value.length < 20 || value.length > 64) return false
  const parsed = Date.parse(value)
  return Number.isFinite(parsed)
}

function positiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 1
}

function nonnegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}
