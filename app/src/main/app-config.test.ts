import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { AppConfigStore } from './app-config'

describe('AppConfigStore', () => {
  let dir: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'pr-app-config-'))
  })
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  const storeAt = (path: string) => new AppConfigStore(path)

  it('reports first run incomplete when no config file exists', () => {
    expect(storeAt(join(dir, 'app-config.json')).isFirstRunComplete()).toBe(false)
  })

  it('persists the first-run flag across store instances', () => {
    const path = join(dir, 'app-config.json')
    storeAt(path).markFirstRunComplete()
    expect(storeAt(path).isFirstRunComplete()).toBe(true)
    expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual({ first_run_complete: true })
  })

  it('creates missing parent directories on write', () => {
    const path = join(dir, 'nested', 'deeper', 'app-config.json')
    storeAt(path).markFirstRunComplete()
    expect(storeAt(path).isFirstRunComplete()).toBe(true)
  })

  it('treats corrupt config as empty instead of throwing, and logs', () => {
    const path = join(dir, 'app-config.json')
    writeFileSync(path, '{not json')
    const logged: string[] = []
    const store = new AppConfigStore(path, (m) => logged.push(m))
    expect(store.isFirstRunComplete()).toBe(false)
    expect(logged.some((m) => m.includes('unreadable'))).toBe(true)
    store.markFirstRunComplete()
    expect(store.isFirstRunComplete()).toBe(true)
  })

  it('treats an array config as empty instead of spreading its indices', () => {
    const path = join(dir, 'app-config.json')
    writeFileSync(path, JSON.stringify(['not', 'a', 'config']))
    const store = storeAt(path)
    expect(store.isFirstRunComplete()).toBe(false)
    store.markFirstRunComplete()
    // The array must not leak into the rewritten config as index keys.
    expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual({ first_run_complete: true })
  })

  it('preserves unknown keys written by a newer app version', () => {
    const path = join(dir, 'app-config.json')
    writeFileSync(path, JSON.stringify({ future_knob: 'keep-me' }))
    storeAt(path).markFirstRunComplete()
    expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual({
      future_knob: 'keep-me',
      first_run_complete: true
    })
  })

  it('persists private web access only after an explicit opt-in', () => {
    const path = join(dir, 'app-config.json')
    const store = storeAt(path)
    expect(store.privateWebEnabled()).toBe(false)
    store.setPrivateWebEnabled(true)
    expect(storeAt(path).privateWebEnabled()).toBe(true)
    store.setPrivateWebEnabled(false)
    expect(storeAt(path).privateWebEnabled()).toBe(false)
  })
})
