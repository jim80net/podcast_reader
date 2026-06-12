import { describe, expect, it } from 'vitest'

import { parseProtocolUrl, selectProtocolArgv } from './protocol'

describe('parseProtocolUrl', () => {
  it('accepts podcast-reader://transcribe?url=<http(s) url>', () => {
    expect(
      parseProtocolUrl('podcast-reader://transcribe?url=https%3A%2F%2Fexample.com%2Fv')
    ).toEqual({ url: 'https://example.com/v' })
    expect(parseProtocolUrl('podcast-reader://transcribe?url=http://example.com/a')).toEqual({
      url: 'http://example.com/a'
    })
  })

  it('rejects other schemes', () => {
    expect(parseProtocolUrl('https://transcribe?url=https://example.com')).toBeNull()
    expect(parseProtocolUrl('podcast-readerx://transcribe?url=https://example.com')).toBeNull()
  })

  it('rejects other hosts', () => {
    expect(parseProtocolUrl('podcast-reader://settings?url=https://example.com')).toBeNull()
    expect(parseProtocolUrl('podcast-reader://?url=https://example.com')).toBeNull()
  })

  it('rejects a missing, empty, or non-http(s) url param', () => {
    expect(parseProtocolUrl('podcast-reader://transcribe')).toBeNull()
    expect(parseProtocolUrl('podcast-reader://transcribe?url=')).toBeNull()
    expect(parseProtocolUrl('podcast-reader://transcribe?url=file:///etc/passwd')).toBeNull()
    expect(parseProtocolUrl('podcast-reader://transcribe?url=javascript:alert(1)')).toBeNull()
    expect(parseProtocolUrl('podcast-reader://transcribe?url=notaurl')).toBeNull()
  })

  it('rejects garbage input without throwing', () => {
    expect(parseProtocolUrl('')).toBeNull()
    expect(parseProtocolUrl('::::')).toBeNull()
  })

  it('rejects a spoofed host with transcribe in the path', () => {
    expect(parseProtocolUrl('podcast-reader://evil.com/transcribe?url=https://e.com')).toBeNull()
  })

  it('rejects embedded credentials (userinfo)', () => {
    expect(
      parseProtocolUrl('podcast-reader://user:pass@transcribe?url=https://example.com')
    ).toBeNull()
    expect(parseProtocolUrl('podcast-reader://user@transcribe?url=https://example.com')).toBeNull()
  })

  it('rejects an explicit port', () => {
    expect(parseProtocolUrl('podcast-reader://transcribe:8080?url=https://example.com')).toBeNull()
  })

  it('rejects extra path segments beyond the bare host', () => {
    expect(
      parseProtocolUrl('podcast-reader://transcribe/extra?url=https://example.com')
    ).toBeNull()
    expect(
      parseProtocolUrl('podcast-reader://transcribe/../transcribe?url=https://example.com')
    ).toBeNull()
  })

  it('still accepts a bare trailing slash', () => {
    expect(parseProtocolUrl('podcast-reader://transcribe/?url=https://example.com')).toEqual({
      url: 'https://example.com'
    })
  })

  it('rejects a double-encoded url param (decodes to a non-URL)', () => {
    expect(
      parseProtocolUrl('podcast-reader://transcribe?url=https%253A%252F%252Fevil.com')
    ).toBeNull()
  })
})

describe('selectProtocolArgv', () => {
  it('selects the commandLine entry matching the protocol (per P8: never pop blindly)', () => {
    const argv = [
      'C:\\app\\PodcastReader.exe',
      '--allow-file-access',
      'podcast-reader://transcribe?url=https://example.com/v'
    ]
    expect(selectProtocolArgv(argv)).toBe('podcast-reader://transcribe?url=https://example.com/v')
  })

  it('returns null when no entry matches', () => {
    expect(selectProtocolArgv(['exe', '--flag', 'C:\\some\\file.mp3'])).toBeNull()
    expect(selectProtocolArgv([])).toBeNull()
  })

  it('matches the scheme case-insensitively', () => {
    expect(selectProtocolArgv(['exe', 'PODCAST-READER://transcribe?url=http://e.com'])).toBe(
      'PODCAST-READER://transcribe?url=http://e.com'
    )
  })
})
