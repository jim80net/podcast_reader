import { describe, expect, it } from 'vitest'

import { registrableDomain, registrableDomainOfUrl } from './etld'

describe('registrableDomain', () => {
  it('returns two-label hosts as-is', () => {
    expect(registrableDomain('x.com')).toBe('x.com')
    expect(registrableDomain('example.com')).toBe('example.com')
  })

  it('reduces subdomains to the registrable domain (per U4)', () => {
    expect(registrableDomain('media.example.com')).toBe('example.com')
    expect(registrableDomain('www.youtube.com')).toBe('youtube.com')
    expect(registrableDomain('a.b.c.example.org')).toBe('example.org')
  })

  it('keeps three labels under ccTLD generic second levels', () => {
    expect(registrableDomain('www.bbc.co.uk')).toBe('bbc.co.uk')
    expect(registrableDomain('bbc.co.uk')).toBe('bbc.co.uk')
    expect(registrableDomain('iview.abc.net.au')).toBe('abc.net.au')
    expect(registrableDomain('video.nico.co.jp')).toBe('nico.co.jp')
  })

  it('is case-insensitive and tolerates a trailing dot', () => {
    expect(registrableDomain('Media.Example.COM')).toBe('example.com')
    expect(registrableDomain('example.com.')).toBe('example.com')
  })

  it('returns null when no registrable domain exists', () => {
    expect(registrableDomain('localhost')).toBeNull()
    expect(registrableDomain('127.0.0.1')).toBeNull()
    expect(registrableDomain('[::1]')).toBeNull()
    expect(registrableDomain('')).toBeNull()
  })
})

describe('registrableDomainOfUrl', () => {
  it('derives from the URL host (the subdomain-source scenario, per U4)', () => {
    expect(registrableDomainOfUrl('https://media.example.com/clip')).toBe('example.com')
    expect(registrableDomainOfUrl('https://x.com/user/status/1')).toBe('x.com')
  })

  it('returns null for non-http(s) and unparseable URLs', () => {
    expect(registrableDomainOfUrl('chrome://extensions')).toBeNull()
    expect(registrableDomainOfUrl('file:///audio.mp3')).toBeNull()
    expect(registrableDomainOfUrl('not a url')).toBeNull()
  })
})
