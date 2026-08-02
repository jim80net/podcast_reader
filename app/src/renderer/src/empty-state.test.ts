import { describe, expect, it } from 'vitest'

import { emptyLibraryState } from './empty-state'
import { hrefFor, parseHash } from './router'

describe('emptyLibraryState', () => {
  it('provides quiet editorial copy without an ornamental mark', () => {
    const state = emptyLibraryState()
    expect(state.title).toBe('Start your library')
    expect(state.lead.length).toBeGreaterThan(0)
    expect(state).not.toHaveProperty('mark')
  })

  it('points the primary CTA at the New view', () => {
    const state = emptyLibraryState()
    expect(state.cta.label).toBe('New transcript')
    expect(state.cta.href).toBe(hrefFor({ view: 'new' }))
    // And that href round-trips to the New route (no brittle string coupling).
    expect(parseHash(state.cta.href)).toEqual({ view: 'new' })
  })
})
