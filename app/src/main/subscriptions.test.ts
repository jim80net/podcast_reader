import { describe, expect, it } from 'vitest'

import { EngineRequestError } from './engine-client'
import { publicSubscription, subscriptionError, validateFeedUrlInput, validateSubscriptionId } from './subscriptions'
import type { EngineSubscriptionRecord } from './engine-client'

const record = (overrides: Partial<EngineSubscriptionRecord> = {}): EngineSubscriptionRecord => ({
  id: 'sub_0123456789abcdef0123456789abcdef',
  feed_url: 'https://private.example/feed.xml?secret=yes',
  enabled: true,
  title: 'Private show',
  normalized_origin: 'https://private.example',
  etag: 'secret-etag',
  last_modified: null,
  last_checked_at: '2026-08-03T00:00:00Z',
  next_check_at: '2026-08-03T00:30:00Z',
  last_error: null,
  last_error_at: null,
  created_at: '2026-08-03T00:00:00Z',
  updated_at: '2026-08-03T00:00:00Z',
  ...overrides
})

describe('subscription IPC containment', () => {
  it('projects only renderer-safe local fields', () => {
    const projected = publicSubscription(record())
    expect(projected).toEqual({
      id: 'sub_0123456789abcdef0123456789abcdef',
      title: 'Private show',
      origin: 'https://private.example',
      lastCheckedAt: '2026-08-03T00:00:00Z',
      nextCheckAt: '2026-08-03T00:30:00Z',
      lastErrorCode: null
    })
    expect(JSON.stringify(projected)).not.toContain('secret')
    expect(JSON.stringify(projected)).not.toContain('feed.xml')
  })

  it('replaces engine details with bounded fixed error codes', () => {
    const mapped = subscriptionError(new EngineRequestError(400, 'bad https://private.example/feed.xml'))
    expect(mapped.message).toBe('invalid_feed')
    expect(mapped.message).not.toContain('private.example')
  })

  it('rejects malformed renderer inputs before transport', () => {
    expect(() => validateFeedUrlInput('')).toThrow('invalid_feed_url')
    expect(() => validateSubscriptionId('../jobs')).toThrow('invalid_subscription_id')
  })
})
