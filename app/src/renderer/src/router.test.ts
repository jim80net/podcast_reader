import { describe, expect, it } from 'vitest'

import { hrefFor, parseHash } from './router'

describe('parseHash', () => {
  it('defaults to the Library view', () => {
    expect(parseHash('')).toEqual({ view: 'library' })
    expect(parseHash('#/')).toEqual({ view: 'library' })
    expect(parseHash('#/library')).toEqual({ view: 'library' })
  })

  it('parses the five views', () => {
    expect(parseHash('#/new')).toEqual({ view: 'new' })
    expect(parseHash('#/settings')).toEqual({ view: 'settings' })
    expect(parseHash('#/setup')).toEqual({ view: 'setup' })
    expect(parseHash('#/reader/abc123')).toEqual({ view: 'reader', sourceId: 'abc123' })
  })

  it('URI-decodes the reader source id', () => {
    expect(parseHash('#/reader/a%2Fb%20c')).toEqual({ view: 'reader', sourceId: 'a/b c' })
  })

  it('falls back to Library on unknown or incomplete routes', () => {
    expect(parseHash('#/bogus')).toEqual({ view: 'library' })
    expect(parseHash('#/reader')).toEqual({ view: 'library' })
    expect(parseHash('#/reader/')).toEqual({ view: 'library' })
    expect(parseHash('#/reader/%E0%A4%A')).toEqual({ view: 'library' }) // malformed escape
  })
})

describe('hrefFor', () => {
  it('round-trips through parseHash', () => {
    for (const route of [
      { view: 'library' } as const,
      { view: 'new' } as const,
      { view: 'settings' } as const,
      { view: 'setup' } as const,
      { view: 'reader', sourceId: 'a/b c' } as const
    ]) {
      expect(parseHash(hrefFor(route))).toEqual(route)
    }
  })
})
