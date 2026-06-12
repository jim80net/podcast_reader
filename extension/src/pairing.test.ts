import { describe, expect, it } from 'vitest'

import {
  CODE_ALPHABET,
  parseCombined,
  parseFields,
  performPairing,
  resolvePairingInput
} from './pairing'

describe('parseCombined', () => {
  it('parses the <port>-<code> paste string, uppercasing the code', () => {
    expect(parseCombined('51234-abc234')).toEqual({ port: 51234, code: 'ABC234' })
    expect(parseCombined('  8080-XYZ789  ')).toEqual({ port: 8080, code: 'XYZ789' })
  })

  it('rejects malformed strings', () => {
    expect(parseCombined('')).toBeNull()
    expect(parseCombined('51234')).toBeNull()
    expect(parseCombined('51234-ABCDE')).toBeNull() // 5-char code
    expect(parseCombined('51234-ABC2345')).toBeNull() // 7-char code
    expect(parseCombined('no-ABC234')).toBeNull()
    expect(parseCombined('51234-ABC234-extra')).toBeNull()
  })

  it('rejects ports outside 1-65535', () => {
    expect(parseCombined('0-ABC234')).toBeNull()
    expect(parseCombined('65536-ABC234')).toBeNull()
    expect(parseCombined('65535-ABC234')).toEqual({ port: 65535, code: 'ABC234' })
  })

  it('rejects characters outside the unambiguous alphabet', () => {
    // engine/pairing.py CODE_ALPHABET: no 0/O, no 1/I/L, no U.
    expect(parseCombined('51234-ABC230')).toBeNull()
    expect(parseCombined('51234-ABC23O')).toBeNull()
    expect(parseCombined('51234-ABC23I')).toBeNull()
    expect(parseCombined('51234-ABC23L')).toBeNull()
    expect(parseCombined('51234-ABC23U')).toBeNull()
    expect(CODE_ALPHABET).not.toMatch(/[01OILU]/)
  })
})

describe('parseFields', () => {
  it('parses separate port and code fields', () => {
    expect(parseFields('51234', 'abc234')).toEqual({ port: 51234, code: 'ABC234' })
  })

  it('rejects non-numeric ports and bad codes', () => {
    expect(parseFields('port', 'ABC234')).toBeNull()
    expect(parseFields('51234', '')).toBeNull()
    expect(parseFields('', 'ABC234')).toBeNull()
  })
})

describe('resolvePairingInput', () => {
  it('prefers a non-empty combined string', () => {
    expect(resolvePairingInput('51234-ABC234', '9', 'XXXXXX')).toEqual({
      port: 51234,
      code: 'ABC234'
    })
  })

  it('falls back to the separate fields when the combined input is empty', () => {
    expect(resolvePairingInput('', '51234', 'ABC234')).toEqual({ port: 51234, code: 'ABC234' })
  })

  it('does not fall back when a non-empty combined string is malformed', () => {
    expect(resolvePairingInput('garbage', '51234', 'ABC234')).toBeNull()
  })
})

describe('performPairing', () => {
  const input = { port: 51234, code: 'ABC234' }

  it('claims, verifies, and returns a storable pairing', async () => {
    const seen: string[] = []
    const result = await performPairing(input, {
      claim: (port, code) => {
        seen.push(`claim ${port} ${code}`)
        return Promise.resolve('tok-1')
      },
      verify: (pairing) => {
        seen.push(`verify ${pairing.port} ${pairing.token}`)
        return Promise.resolve({})
      }
    })
    expect(result).toEqual({ ok: true, pairing: { port: 51234, token: 'tok-1' } })
    expect(seen).toEqual(['claim 51234 ABC234', 'verify 51234 tok-1'])
  })

  it('classifies an HTTP rejection (uniform 403) as rejected', async () => {
    const result = await performPairing(input, {
      claim: () => Promise.reject(Object.assign(new Error('403'), { status: 403 })),
      verify: () => Promise.resolve({})
    })
    expect(result).toEqual({ ok: false, reason: 'rejected' })
  })

  it('classifies a network failure as unreachable', async () => {
    const result = await performPairing(input, {
      claim: () => Promise.reject(new TypeError('fetch failed')),
      verify: () => Promise.resolve({})
    })
    expect(result).toEqual({ ok: false, reason: 'unreachable' })
  })

  it('fails verify without producing a pairing (prior pairing stays untouched)', async () => {
    const result = await performPairing(input, {
      claim: () => Promise.resolve('tok-1'),
      verify: () => Promise.reject(new Error('401'))
    })
    expect(result).toEqual({ ok: false, reason: 'verify-failed' })
  })
})
