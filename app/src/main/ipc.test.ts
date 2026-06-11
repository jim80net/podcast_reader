import { describe, expect, it } from 'vitest'

import { registerIpcHandlers } from './ipc'
import { CHANNELS } from '../shared/ipc'
import type { UpdaterAccess } from './ipc'
import type { EngineManager } from './engine-manager'

const fakeUpdates: UpdaterAccess = {
  status: () => ({ state: 'disabled', reason: 'test' }),
  installNow: () => Promise.resolve()
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

function makeManager(opts: { ready?: boolean } = {}) {
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
                return Promise.resolve(`${prop}-result`)
              }
          }
        )
      : null
  const manager = {
    client,
    status: { state: client !== null ? 'ready' : 'starting' },
    keyStorageMode: 'encrypted',
    putKey: (...args: unknown[]) => {
      calls.push(['manager.putKey', ...args])
      return Promise.resolve()
    }
  }
  return { manager: manager as unknown as EngineManager, calls }
}

describe('registerIpcHandlers', () => {
  it('registers a handler for every channel', () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager().manager, fakeUpdates)
    for (const channel of Object.values(CHANNELS)) {
      expect(reg.handlers.has(channel), `missing handler: ${channel}`).toBe(true)
    }
  })

  it('maps job submission to the snake_case engine body', async () => {
    const reg = makeRegistrar()
    const { manager, calls } = makeManager()
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates)
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
    registerIpcHandlers(reg.ipcMain, manager, fakeUpdates)
    await reg.invoke(CHANNELS.keysPut, 'anthropic', 'sk-1')
    expect(calls).toContainEqual(['manager.putKey', 'anthropic', 'sk-1'])
    expect(calls.find((c) => c[0] === 'putKey')).toBeUndefined()
  })

  it('rejects engine requests while the engine is not ready', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates)
    await expect(reg.invoke(CHANNELS.jobsList)).rejects.toThrow(/not ready/i)
  })

  it('answers status and key-storage-mode without a ready engine', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates)
    await expect(reg.invoke(CHANNELS.engineGetStatus)).resolves.toEqual({ state: 'starting' })
    await expect(reg.invoke(CHANNELS.keysStorageMode)).resolves.toBe('encrypted')
  })

  it('answers update status without a ready engine', async () => {
    const reg = makeRegistrar()
    registerIpcHandlers(reg.ipcMain, makeManager({ ready: false }).manager, fakeUpdates)
    await expect(reg.invoke(CHANNELS.updateGetStatus)).resolves.toEqual({
      state: 'disabled',
      reason: 'test'
    })
    await expect(reg.invoke(CHANNELS.updateInstall)).resolves.toBeUndefined()
  })
})
