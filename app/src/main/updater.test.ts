import { describe, expect, it } from 'vitest'

import { UpdaterController, updaterGate } from './updater'
import { PUSH_CHANNELS } from '../shared/ipc'
import type { AutoUpdaterLike, UpdaterDeps } from './updater'
import type { UpdateStatus } from '../shared/ipc'

describe('updaterGate', () => {
  it('disables updates in development (unpackaged)', () => {
    const gate = updaterGate({ isPackaged: false, buildSigned: true, env: {} })
    expect(gate.enabled).toBe(false)
    if (!gate.enabled) expect(gate.reason).toContain('development')
  })

  it('disables updates on unsigned packaged builds', () => {
    const gate = updaterGate({ isPackaged: true, buildSigned: false, env: {} })
    expect(gate.enabled).toBe(false)
    if (!gate.enabled) expect(gate.reason).toContain('unsigned')
  })

  it('PODCAST_READER_FORCE_UPDATES=1 overrides the unsigned gate on packaged builds only', () => {
    expect(
      updaterGate({
        isPackaged: true,
        buildSigned: false,
        env: { PODCAST_READER_FORCE_UPDATES: '1' }
      }).enabled
    ).toBe(true)
    expect(
      updaterGate({
        isPackaged: false,
        buildSigned: false,
        env: { PODCAST_READER_FORCE_UPDATES: '1' }
      }).enabled
    ).toBe(false)
  })

  it('enables updates on signed packaged builds', () => {
    expect(updaterGate({ isPackaged: true, buildSigned: true, env: {} }).enabled).toBe(true)
  })
})

// `any` matches the overloaded `on` signatures bivariantly (same pattern as ipc.ts).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Listener = (...args: any[]) => void

class FakeAutoUpdater implements AutoUpdaterLike {
  autoDownload = false
  autoInstallOnAppQuit = true
  checks = 0
  quitAndInstallCalls = 0
  private listeners = new Map<string, Listener[]>()

  on(event: string, listener: Listener): unknown {
    const existing = this.listeners.get(event) ?? []
    this.listeners.set(event, [...existing, listener])
    return this
  }

  emit(event: string, ...args: unknown[]): void {
    for (const listener of this.listeners.get(event) ?? []) listener(...args)
  }

  checkForUpdates(): Promise<unknown> {
    this.checks += 1
    return Promise.resolve(null)
  }

  quitAndInstall(): void {
    this.quitAndInstallCalls += 1
  }
}

interface Harness {
  controller: UpdaterController
  updater: FakeAutoUpdater
  statuses: UpdateStatus[]
  calls: string[]
  rechecks: { fn: () => void; ms: number }[]
}

function harness(opts: { consent?: boolean | Promise<boolean> } = {}): Harness {
  const updater = new FakeAutoUpdater()
  const statuses: UpdateStatus[] = []
  const calls: string[] = []
  const rechecks: { fn: () => void; ms: number }[] = []
  const deps: UpdaterDeps = {
    autoUpdater: updater,
    confirm: () => {
      calls.push('confirm')
      const consent = opts.consent ?? true
      return typeof consent === 'boolean' ? Promise.resolve(consent) : consent
    },
    quitEngine: () => {
      calls.push('quitEngine')
      return Promise.resolve()
    },
    send: (channel, payload) => {
      expect(channel).toBe(PUSH_CHANNELS.updateStatus)
      statuses.push(payload as UpdateStatus)
    },
    log: () => undefined,
    scheduleRepeating: (fn, ms) => rechecks.push({ fn, ms })
  }
  const controller = new UpdaterController(deps)
  return { controller, updater, statuses, calls, rechecks }
}

const flush = (): Promise<void> => new Promise((resolve) => setImmediate(resolve))

describe('UpdaterController', () => {
  it('start() forces background download without auto-install-on-quit, then checks', () => {
    const h = harness()
    h.controller.start()
    expect(h.updater.autoDownload).toBe(true)
    expect(h.updater.autoInstallOnAppQuit).toBe(false)
    expect(h.updater.checks).toBe(1)
    expect(h.rechecks).toHaveLength(1)
  })

  it('broadcasts the download lifecycle as statuses', () => {
    const h = harness()
    h.controller.start()
    h.updater.emit('checking-for-update')
    h.updater.emit('update-available', { version: '0.2.0' })
    expect(h.statuses).toEqual([
      { state: 'checking' },
      { state: 'downloading', version: '0.2.0' }
    ])
    expect(h.controller.status).toEqual({ state: 'downloading', version: '0.2.0' })
  })

  it('consented update runs the engine quit sequence strictly before quitAndInstall', async () => {
    const h = harness({ consent: true })
    h.controller.start()
    h.updater.emit('update-downloaded', { version: '0.2.0' })
    await flush()
    expect(h.calls).toEqual(['confirm', 'quitEngine'])
    expect(h.updater.quitAndInstallCalls).toBe(1)
    expect(h.statuses.at(-1)).toEqual({ state: 'installing', version: '0.2.0' })
  })

  it('declined update defers: no install, app continues, status deferred', async () => {
    const h = harness({ consent: false })
    h.controller.start()
    h.updater.emit('update-downloaded', { version: '0.2.0' })
    await flush()
    expect(h.calls).toEqual(['confirm'])
    expect(h.updater.quitAndInstallCalls).toBe(0)
    expect(h.controller.status).toEqual({ state: 'deferred', version: '0.2.0' })
  })

  it('deferred update re-offered on the periodic re-check', async () => {
    const h = harness({ consent: false })
    h.controller.start()
    h.updater.emit('update-downloaded', { version: '0.2.0' })
    await flush()
    expect(h.rechecks[0]).toBeDefined()
    h.rechecks[0]?.fn()
    expect(h.updater.checks).toBe(2)
  })

  it('installNow() applies a downloaded update without a second consent prompt', async () => {
    const h = harness({ consent: false })
    h.controller.start()
    h.updater.emit('update-downloaded', { version: '0.2.0' })
    await flush()
    await h.controller.installNow()
    expect(h.calls).toEqual(['confirm', 'quitEngine'])
    expect(h.updater.quitAndInstallCalls).toBe(1)
  })

  it('installNow() is a no-op before anything downloaded', async () => {
    const h = harness()
    h.controller.start()
    await h.controller.installNow()
    expect(h.updater.quitAndInstallCalls).toBe(0)
  })

  it('quitAndInstall still runs when the engine quit sequence rejects', async () => {
    const updater = new FakeAutoUpdater()
    const controller = new UpdaterController({
      autoUpdater: updater,
      confirm: () => Promise.resolve(true),
      quitEngine: () => Promise.reject(new Error('engine hung')),
      send: () => undefined,
      log: () => undefined,
      scheduleRepeating: () => undefined
    })
    controller.start()
    updater.emit('update-downloaded', { version: '0.2.0' })
    await flush()
    expect(updater.quitAndInstallCalls).toBe(1)
  })

  it('update errors surface as error status, never throw', () => {
    const h = harness()
    h.controller.start()
    h.updater.emit('error', new Error('network down'))
    expect(h.controller.status).toEqual({ state: 'error', message: 'network down' })
  })

  it('concurrent install attempts collapse to one quitAndInstall', async () => {
    const h = harness({ consent: true })
    h.controller.start()
    h.updater.emit('update-downloaded', { version: '0.2.0' })
    await flush()
    await h.controller.installNow()
    expect(h.updater.quitAndInstallCalls).toBe(1)
  })
})
