import { describe, expect, it } from 'vitest'

import { getThemePref, nextThemePref, resolveTheme } from './app-theme'

function memoryStorage(initial?: Record<string, string>): Storage {
  const map = new Map<string, string>(Object.entries(initial ?? {}))
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (k) => map.get(k) ?? null,
    key: (i) => [...map.keys()][i] ?? null,
    removeItem: (k) => map.delete(k),
    setItem: (k, v) => {
      map.set(k, v)
    }
  }
}

const winWith = (systemDark: boolean): Window =>
  ({ matchMedia: () => ({ matches: systemDark }) }) as unknown as Window

describe('getThemePref', () => {
  it('defaults to system and ignores junk', () => {
    expect(getThemePref(memoryStorage())).toBe('system')
    expect(getThemePref(memoryStorage({ 'pr.theme': 'neon' }))).toBe('system')
  })

  it('reads a stored valid preference', () => {
    expect(getThemePref(memoryStorage({ 'pr.theme': 'light' }))).toBe('light')
    expect(getThemePref(memoryStorage({ 'pr.theme': 'dark' }))).toBe('dark')
  })
})

describe('resolveTheme', () => {
  it('returns the pinned palette verbatim', () => {
    expect(resolveTheme('light', winWith(true))).toBe('light')
    expect(resolveTheme('dark', winWith(false))).toBe('dark')
  })

  it('consults the OS for system', () => {
    expect(resolveTheme('system', winWith(true))).toBe('dark')
    expect(resolveTheme('system', winWith(false))).toBe('light')
  })
})

describe('nextThemePref', () => {
  it('cycles System → Light → Dark → System', () => {
    expect(nextThemePref('system')).toBe('light')
    expect(nextThemePref('light')).toBe('dark')
    expect(nextThemePref('dark')).toBe('system')
  })
})
