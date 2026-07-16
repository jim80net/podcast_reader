import { describe, expect, it } from 'vitest'

import { registerIpcHandlers } from './ipc'
import { CHANNELS } from '../shared/ipc'
import type { AppConfigAccess, UpdaterAccess } from './ipc'
import type { EngineManager } from './engine-manager'

const fakeUpdates: UpdaterAccess = {
  status: () => ({ state: 'disabled', reason: 'test' }),
  installNow: () => Promise.resolve()
}

function makeConfig(initial = false): AppConfigAccess & { complete: boolean } {
  return {
    complete: initial,
    isFirstRunComplete() {
      return this.complete
    },
    markFirstRunComplete() {
      this.complete = true
    }
  }
}

type Handler = (event: unknown, ...args: unknown[]) => unknown

function makeRegistrar() {
  const handlers = new Map<string, Handler>()
  return {
    handlers,
    ipcMain: {
      handle: (channel: string, handler: Handler) => {
        handlers.set(channel, handler)
      }
    },
    // async so sync throws become rejections, as ipcMain.handle treats them
    invoke: async (channel: string, ...args: unknown[]) => {
      const handler = handlers.get(channel)
      if (handler === undefined) throw new Error(`no handler for ${channel}`)
      return await handler({}, ...args) // first arg mimics the IpcMainInvokeEvent
    }
  }
}

function makeManager(opts: { ready?: boolean; results?: Record<string, unknown> } = {}) {
  const calls: unknown[][] = []
  const client =
    (opts.ready ?? true)
      ? new Proxy(
          {},
          {
            get:
              (_t, prop: string) =>
              (...args: unknown[]) => {
                calls.push([prop, ...args])
                return Promise.resolve(opts.results?.[prop] ?? `${prop}-result`)
              }
          }
        )
      : null
  const manager = {
    client,
    port: client !== null ? 51234 : null,
    status: { state: client !== null ? 'ready' : 'starting' },
    keyStorageMode: 'encrypted',
    putKey: (...args: unknown[]) => {
      calls.push(['manager.putKey', ...args])
      return Promise.resolve()
    },
    restart: (...args: unknown[]) => {
      calls.push(['manager.restart', ...args])
      return Promise.resolve()
    }
  }
  return { manager: manager as unknown as EngineManager, calls }
}

describe('registerIpcHandlers', () => {
  it('registers a handler for every channel', () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager().manager, fakeUpdates, makeConfig())
    for (const channel of Object.values(CHANNELS)) {
      expect(reg.handlers.has(channel), `missing handler: ${channel}`).toBe(true)
    }
  })

  it('maps job submission to the snake_case engine body', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await reg.invoke(CHANNELS.jobsSubmit, {
      source: 'https://e.com/v',
      title: 'T',
      requiresConfirmation: true
    })
    expect(calls).toContainEqual([
      'submitJob',
      { source: 'https://e.com/v', title: 'T', requires_confirmation: true }
    ])
  })

  it('routes key writes through the manager (vault + push), never the raw client', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await reg.invoke(CHANNELS.keysPut, 'anthropic', 'sk-1')
    expect(calls).toContainEqual(['manager.putKey', 'anthropic', 'sk-1'])
    expect(calls.find((c) => c[0] === 'putKey')).toBeUndefined()
  })

  it('rejects engine requests while the engine is not ready', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates, makeConfig())
    await expect(reg.invoke(CHANNELS.jobsList)).rejects.toThrow(/not ready/i)
  })

  it('answers status and key-storage-mode without a ready engine', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates, makeConfig())
    await expect(reg.invoke(CHANNELS.engineGetStatus)).resolves.toEqual({ state: 'starting' })
    await expect(reg.invoke(CHANNELS.keysStorageMode)).resolves.toBe('encrypted')
  })

  it('routes pack operations to the engine client', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await reg.invoke(CHANNELS.packsList)
    await reg.invoke(CHANNELS.packsInstall, 'cuda-runtime')
    await reg.invoke(CHANNELS.packsUninstall, 'model-tiny')
    expect(calls).toContainEqual(['listPacks'])
    expect(calls).toContainEqual(['installPack', 'cuda-runtime'])
    expect(calls).toContainEqual(['uninstallPack', 'model-tiny'])
  })

  it('routes media info to the engine client', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await reg.invoke(CHANNELS.mediaInfo, 'abc123')
    expect(calls).toContainEqual(['mediaInfo', 'abc123'])
  })

  it('routes private library search through the credential-free bridge', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await reg.invoke(CHANNELS.librarySearch, 'private phrase')
    expect(calls).toContainEqual(['searchLibrary', 'private phrase'])
  })

  it('composes the pairing display from the engine mint and the manager port', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager({
      results: { mintPairing: { code: 'ABC234', expires_at: 1234.5 } }
    })
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await expect(reg.invoke(CHANNELS.pairStart)).resolves.toEqual({
      port: 51234,
      code: 'ABC234',
      expires_at: 1234.5
    })
    expect(calls).toContainEqual(['mintPairing'])
  })

  it('rejects pairing while the engine is not ready', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates, makeConfig())
    await expect(reg.invoke(CHANNELS.pairStart)).rejects.toThrow(/not ready/i)
  })

  it('routes cookie-jar listing and deletion to the engine client', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await reg.invoke(CHANNELS.cookiesList)
    await reg.invoke(CHANNELS.cookiesDelete, 'example.com')
    expect(calls).toContainEqual(['listCookieJars'])
    expect(calls).toContainEqual(['deleteCookieJar', 'example.com'])
  })

  it('serves the first-run flag from the app config, engine-independent', async () => {
    const reg = makeRegistrar()
    const config = makeConfig()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates, config)
    await expect(reg.invoke(CHANNELS.firstRunGet)).resolves.toBe(false)
    await reg.invoke(CHANNELS.firstRunComplete)
    await expect(reg.invoke(CHANNELS.firstRunGet)).resolves.toBe(true)
  })

  it('answers update status without a ready engine', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates, makeConfig())
    await expect(reg.invoke(CHANNELS.updateGetStatus)).resolves.toEqual({
      state: 'disabled',
      reason: 'test'
    })
    await expect(reg.invoke(CHANNELS.updateInstall)).resolves.toBeUndefined()
  })

  it('routes engine restart to the manager even without a ready engine', async () => {
    const reg = makeRegistrar()
    // Manual restart is the recovery from `failed` — the engine is NOT ready.
    const { manager, calls } = makeManager({ ready: false })
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates, makeConfig())
    await expect(reg.invoke(CHANNELS.engineRestart)).resolves.toBeUndefined()
    expect(calls).toContainEqual(['manager.restart'])
  })
})
