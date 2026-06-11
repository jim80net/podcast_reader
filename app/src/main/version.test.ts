import { describe, expect, it } from 'vitest'

import { MIN_ENGINE_VERSION, versionAtLeast } from './version'

describe('versionAtLeast', () => {
  it('accepts an equal version', () => {
    expect(versionAtLeast('0.1.0', '0.1.0')).toBe(true)
  })

  it('accepts newer versions (per P3/Q1: newer engines are adopted)', () => {
    expect(versionAtLeast('0.2.0', '0.1.0')).toBe(true)
    expect(versionAtLeast('1.0.0', '0.9.9')).toBe(true)
    expect(versionAtLeast('0.1.1', '0.1.0')).toBe(true)
  })

  it('rejects older versions', () => {
    expect(versionAtLeast('0.0.9', '0.1.0')).toBe(false)
    expect(versionAtLeast('0.1.0', '0.2.0')).toBe(false)
  })

  it('compares numerically, not lexically', () => {
    expect(versionAtLeast('0.10.0', '0.9.0')).toBe(true)
  })

  it('handles cores of different lengths', () => {
    expect(versionAtLeast('1.0', '1.0.0')).toBe(true)
    expect(versionAtLeast('1.0.0.1', '1.0.0')).toBe(true)
  })

  it('treats a pre-release suffix as below the plain release of the same core', () => {
    // engine_version() returns "0.0.0-dev" when the package is not installed
    expect(versionAtLeast('0.0.0-dev', MIN_ENGINE_VERSION)).toBe(false)
    expect(versionAtLeast('0.1.0-rc1', '0.1.0')).toBe(false)
    expect(versionAtLeast('0.2.0-dev', '0.1.0')).toBe(true)
  })

  it('rejects unparseable versions outright', () => {
    expect(versionAtLeast('', '0.1.0')).toBe(false)
    expect(versionAtLeast('garbage', '0.1.0')).toBe(false)
  })
})

describe('MIN_ENGINE_VERSION', () => {
  it('is a plain dotted version', () => {
    expect(MIN_ENGINE_VERSION).toMatch(/^\d+(\.\d+)*$/)
  })

  it('puts a 0.1.0 engine (lacking shutdown/providers/keys-test) below the floor', () => {
    expect(versionAtLeast('0.1.0', MIN_ENGINE_VERSION)).toBe(false)
  })

  it('admits 0.2.0 and newer engines', () => {
    expect(versionAtLeast('0.2.0', MIN_ENGINE_VERSION)).toBe(true)
    expect(versionAtLeast('0.2.1', MIN_ENGINE_VERSION)).toBe(true)
    expect(versionAtLeast('1.0.0', MIN_ENGINE_VERSION)).toBe(true)
  })
})
