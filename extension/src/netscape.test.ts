import { describe, expect, it } from 'vitest'

import { serializeNetscape, uniqueCookies } from './netscape'
import type { CapturedCookie } from './netscape'

function cookie(overrides: Partial<CapturedCookie> = {}): CapturedCookie {
  return {
    domain: '.example.com',
    path: '/',
    secure: true,
    httpOnly: false,
    session: false,
    expirationDate: 1900000000.75,
    name: 'sid',
    value: 'abc123',
    ...overrides
  }
}

describe('serializeNetscape', () => {
  it('writes the header and 7 tab-separated fields per cookie', () => {
    const jar = serializeNetscape([cookie()])
    const lines = jar.trimEnd().split('\n')
    expect(lines[0]).toBe('# Netscape HTTP Cookie File')
    expect(lines[1]).toBe('.example.com\tTRUE\t/\tTRUE\t1900000000\tsid\tabc123')
    expect(jar.endsWith('\n')).toBe(true)
  })

  it('sets the subdomain flag from the leading dot', () => {
    const jar = serializeNetscape([cookie({ domain: 'example.com' })])
    expect(jar).toContain('example.com\tFALSE\t/')
  })

  it('prefixes httpOnly cookies with #HttpOnly_', () => {
    const jar = serializeNetscape([cookie({ httpOnly: true })])
    expect(jar).toContain('#HttpOnly_.example.com\tTRUE\t/\tTRUE\t1900000000\tsid\tabc123')
  })

  it('writes 0 expiry for session cookies and missing expirations', () => {
    expect(serializeNetscape([cookie({ session: true })])).toContain('\t0\tsid\t')
    expect(
      serializeNetscape([cookie({ session: false, expirationDate: undefined })])
    ).toContain('\t0\tsid\t')
  })

  it('marks insecure cookies FALSE in the secure field', () => {
    const jar = serializeNetscape([cookie({ secure: false })])
    expect(jar).toContain('\t/\tFALSE\t1900000000')
  })
})

describe('uniqueCookies', () => {
  it('unions the url-keyed and domain-keyed queries, first occurrence winning', () => {
    const byUrl = [cookie({ name: 'sid', value: 'from-url' }), cookie({ name: 'a' })]
    const byDomain = [
      cookie({ name: 'sid', value: 'from-domain' }), // duplicate identity
      cookie({ name: 'b', domain: '.login.example.com' }) // sibling subdomain
    ]
    const merged = uniqueCookies(byUrl, byDomain)
    expect(merged.map((c) => c.name)).toEqual(['sid', 'a', 'b'])
    expect(merged[0]?.value).toBe('from-url')
  })

  it('distinguishes cookies by the (name, domain, path) triple', () => {
    const merged = uniqueCookies(
      [cookie({ name: 'sid', path: '/' })],
      [cookie({ name: 'sid', path: '/app' })]
    )
    expect(merged).toHaveLength(2)
  })
})
