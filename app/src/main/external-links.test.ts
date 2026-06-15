import { describe, expect, it } from 'vitest'

import { YOUTUBE_REFERER, YOUTUBE_URL_FILTER, isExternalWebUrl } from './external-links'

describe('isExternalWebUrl', () => {
  it('matches http and https URLs (these open in the OS browser)', () => {
    expect(isExternalWebUrl('https://www.youtube.com/watch?v=abc')).toBe(true)
    expect(isExternalWebUrl('http://localhost:8000/docs')).toBe(true)
  })

  it('rejects the app and engine origins so they never escape to a browser', () => {
    // The renderer itself, the media proxy, and the deep-link scheme must stay
    // in-app — only http(s) is "external".
    expect(isExternalWebUrl('file:///app/renderer/index.html')).toBe(false)
    expect(isExternalWebUrl('app://media/deadbeef')).toBe(false)
    expect(isExternalWebUrl('podcast-reader://add?url=x')).toBe(false)
  })

  it('rejects junk rather than throwing', () => {
    expect(isExternalWebUrl('not a url')).toBe(false)
    expect(isExternalWebUrl('')).toBe(false)
  })
})

describe('YouTube Referer rules', () => {
  it('uses a valid https Referer (file:// sends none, triggering Error 153)', () => {
    expect(YOUTUBE_REFERER).toMatch(/^https:\/\//)
  })

  it('scopes the filter to YouTube hosts only (engine + app traffic untouched)', () => {
    expect(YOUTUBE_URL_FILTER.urls).toContain('https://*.youtube-nocookie.com/*')
    // No wildcard-everything entry that would spoof a Referer onto 127.0.0.1.
    expect(YOUTUBE_URL_FILTER.urls.every((u) => u.includes('youtube') || u.includes('ytimg') || u.includes('googlevideo'))).toBe(true)
  })
})
