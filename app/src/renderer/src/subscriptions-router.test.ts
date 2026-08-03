import { describe, expect, it } from 'vitest'

import { hrefFor, parseHash } from './router'

describe('Subscriptions route', () => {
  it('round-trips as a first-class view', () => {
    expect(parseHash('#/subscriptions')).toEqual({ view: 'subscriptions' })
    expect(hrefFor({ view: 'subscriptions' })).toBe('#/subscriptions')
  })
})
