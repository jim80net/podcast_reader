import { describe, expect, it } from 'vitest'

import { captureTarget } from './capture'

describe('captureTarget', () => {
  it('targets the registrable domain of a subdomain source (per U4)', () => {
    const target = captureTarget('https://media.example.com/clip')
    expect(target).toEqual({
      domain: 'example.com',
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
