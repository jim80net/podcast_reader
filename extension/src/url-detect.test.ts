import { describe, expect, it } from 'vitest'

import { classifySource, sourceLabel } from './url-detect'

describe('classifySource (mirrors pipeline.classify_input)', () => {
  it('classifies YouTube URLs (youtube.com and youtu.be)', () => {
    expect(classifySource('https://www.youtube.com/watch?v=abc')).toBe('youtube')
    expect(classifySource('https://youtu.be/abc')).toBe('youtube')
    expect(classifySource('http://m.youtube.com/watch?v=abc')).toBe('youtube')
  })

  it('classifies other http(s) URLs as generic yt-dlp sources', () => {
    expect(classifySource('https://x.com/user/status/1')).toBe('url')
    expect(classifySource('https://vimeo.com/123')).toBe('url')
    expect(classifySource('http://example.com/episode.mp3')).toBe('url')
  })

  it('marks browser-internal and undefined URLs ineligible', () => {
    expect(classifySource('chrome://extensions')).toBe('ineligible')
    expect(classifySource('about:blank')).toBe('ineligible')
    expect(classifySource('file:///home/me/audio.mp3')).toBe('ineligible')
    expect(classifySource('chrome-extension://abc/popup.html')).toBe('ineligible')
    expect(classifySource(undefined)).toBe('ineligible')
  })
})

describe('sourceLabel', () => {
  it('labels the submit affordance per source kind', () => {
    expect(sourceLabel('youtube')).toBe('Transcribe this YouTube video')
    expect(sourceLabel('url')).toBe('Transcribe this page')
  })
})
