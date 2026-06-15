import { describe, expect, it } from 'vitest'

import { emptyLibraryState } from './empty-state'
import { hrefFor, parseHash } from './router'

describe('emptyLibraryState', () => {
  it('provides branded copy: a mark, a title, and a value-prop lead', () => {
    const state = emptyLibraryState()
    expect(state.mark.length).toBeGreaterThan(0)
    expect(state.title.length).toBeGreaterThan(0)
    expect(state.lead.length).toBeGreaterThan(0)
  })

  it('points the primary CTA at the New view', () => {
    const state = emptyLibraryState()
    expect(state.cta.label).toBe('Transcribe your first episode')
    expect(state.cta.href).toBe(hrefFor({ view: 'new' }))
    // And that href round-trips to the New route (no brittle string coupling).
    expect(parseHash(state.cta.href)).toEqual({ view: 'new' })
  })
})
