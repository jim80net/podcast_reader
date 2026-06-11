import { describe, expect, it } from 'vitest'

import { parseDiscovery, parseEngineState, tokenFingerprint } from './discovery'

const VALID_DISCOVERY = JSON.stringify({
  port: 51234,
  pid: 4242,
  token_fingerprint: 'abcd1234abcd1234',
  version: '0.1.0'
})

describe('parseDiscovery', () => {
  it('parses a valid discovery file', () => {
    expect(parseDiscovery(VALID_DISCOVERY)).toEqual({
      port: 51234,
      pid: 4242,
      token_fingerprint: 'abcd1234abcd1234',
      version: '0.1.0'
    })
  })

  it.each([
    ['not json', 'nope{'],
    ['not an object', '[1]'],
    ['missing port', '{"pid":1,"token_fingerprint":"x","version":"1"}'],
    ['non-integer port', '{"port":"80","pid":1,"token_fingerprint":"x","version":"1"}'],
    ['non-integer pid', '{"port":80,"pid":1.5,"token_fingerprint":"x","version":"1"}'],
    ['non-string fingerprint', '{"port":80,"pid":1,"token_fingerprint":7,"version":"1"}'],
    ['non-string version', '{"port":80,"pid":1,"token_fingerprint":"x","version":1}']
  ])('rejects %s', (_label, text) => {
    expect(() => parseDiscovery(text)).toThrow()
  })
})

describe('parseEngineState', () => {
  it('parses {port, token}', () => {
    expect(parseEngineState('{"port": 51234, "token": "secret"}')).toEqual({
      port: 51234,
      token: 'secret'
    })
  })

  it('rejects a missing or empty token', () => {
    expect(() => parseEngineState('{"port": 1}')).toThrow()
    expect(() => parseEngineState('{"port": 1, "token": ""}')).toThrow()
  })
})

describe('tokenFingerprint', () => {
  it('mirrors engine/settings.py token_fingerprint (sha256 hex, first 16 chars)', () => {
    // python: hashlib.sha256(b"secret").hexdigest()[:16]
    expect(tokenFingerprint('secret')).toBe('2bb80d537b1da3e3')
  })

  it('is 16 lowercase hex chars', () => {
    expect(tokenFingerprint('another-token')).toMatch(/^[0-9a-f]{16}$/)
  })
})
