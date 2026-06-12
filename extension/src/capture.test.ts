import { describe, expect, it } from 'vitest'

import { captureTarget, declaredDomain } from './capture'
import type { CaptureTarget } from './capture'

const cookie = (domain: string): { domain: string } => ({ domain })

const mustTarget = (url: string): CaptureTarget => {
  const target = captureTarget(url)
  if (target === null) throw new Error(`no capture target for ${url}`)
  return target
}

describe('captureTarget', () => {
  it('targets the registrable domain of a subdomain source (per U4)', () => {
    const target = captureTarget('https://media.example.com/clip')
    // sourceHost (new in V1) carries the host that declaredDomain
    // suffix-matches captured cookie domains against.
    expect(target).toEqual({
      domain: 'example.com',
      sourceHost: 'media.example.com',
      origins: ['https://example.com/*', 'https://*.example.com/*'],
      queryUrl: 'https://media.example.com/clip'
    })
  })

  it('requests https origins only (per U6), normalizing http sources', () => {
    const target = captureTarget('http://x.com/user/status/1')
    expect(target?.origins.every((o) => o.startsWith('https://'))).toBe(true)
    expect(target?.queryUrl).toBe('https://x.com/user/status/1')
  })

  it('returns null when no registrable domain exists', () => {
    expect(captureTarget('http://localhost:8080/audio')).toBeNull()
    expect(captureTarget('https://127.0.0.1/audio')).toBeNull()
    expect(captureTarget('file:///audio.mp3')).toBeNull()
  })
})

describe('declaredDomain', () => {
  // V1: the heuristic over-deepens on real registrable domains whose second
  // level collides with the ccTLD generic list (web.de, id.me). The declared
  // domain must come from the captured data, or the parent-domain login
  // cookie fails the engine's suffix validation and the whole PUT 400s.
  it('broadens to a captured parent-domain cookie (web.de collision)', () => {
    const target = mustTarget('https://mail.web.de/clip')
    expect(target.domain).toBe('mail.web.de') // the over-deep heuristic guess
    expect(declaredDomain(target, [cookie('.web.de'), cookie('mail.web.de')])).toBe('web.de')
  })

  it('broadens to a captured parent-domain cookie (id.me collision)', () => {
    const target = mustTarget('https://api.id.me/session')
    expect(target.domain).toBe('api.id.me') // the over-deep heuristic guess
    expect(declaredDomain(target, [cookie('.id.me')])).toBe('id.me')
  })

  it('falls back to the heuristic when no cookie is broader', () => {
    const target = mustTarget('https://mail.web.de/clip')
    expect(declaredDomain(target, [])).toBe('mail.web.de')
    expect(declaredDomain(target, [cookie('mail.web.de')])).toBe('mail.web.de')
  })

  it('never narrows below the heuristic (domain-keyed siblings must stay valid)', () => {
    // A host-only media.example.com cookie is deeper than the example.com
    // heuristic; declaring it would invalidate sibling-subdomain cookies the
    // domain-keyed getAll returned.
    const target = mustTarget('https://media.example.com/clip')
    expect(declaredDomain(target, [cookie('media.example.com')])).toBe('example.com')
  })

  it('ignores cookie domains that do not suffix-match the source host', () => {
    // The domain-keyed query can return cookies on deeper sibling subdomains
    // (e.g. sso.mail.web.de from a mail.web.de source); declaring one would
    // invalidate the rest of the jar. Foreign domains never qualify either.
    const target = mustTarget('https://mail.web.de/clip')
    expect(declaredDomain(target, [cookie('.sso.mail.web.de')])).toBe('mail.web.de')
    expect(declaredDomain(target, [cookie('.evil.example')])).toBe('mail.web.de')
    expect(declaredDomain(target, [cookie('.b.de')])).toBe('mail.web.de') // not a host suffix
  })

  it('requires at least two labels in a candidate (no public-suffix declarations)', () => {
    const target = mustTarget('https://mail.web.de/clip')
    expect(declaredDomain(target, [cookie('.de')])).toBe('mail.web.de')
  })

  it('dot-strips and lowercases candidate cookie domains', () => {
    const target = mustTarget('https://mail.web.de/clip')
    expect(declaredDomain(target, [cookie('.WEB.de')])).toBe('web.de')
  })
})
