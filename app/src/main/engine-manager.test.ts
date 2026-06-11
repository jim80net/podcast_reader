import { describe, expect, it } from 'vitest'

import { EngineManager } from './engine-manager'
import type { ManagerDeps } from './engine-manager'
import type { EngineHandle } from './engine'
import type { EventStreamHandlers } from './engine-client'

const handleFixture: EngineHandle = {
  port: 50000,
  pid: 4242,
  token: 'tok',
  version: '0.1.0',
  adopted: true,
  posture: 'adopted',
  child: null,
  childExited: null
}

interface World {
  manager: EngineManager
  calls: string[]
  sends: { channel: string; payload: unknown }[]
  handlers: EventStreamHandlers | null
}

function makeWorld(opts: { vaultKeys?: Record<string, string>; ensureFails?: boolean } = {}): World {
  const calls: string[] = []
  const sends: { channel: string; payload: unknown }[] = []
  const world: World = { manager: null as unknown as EngineManager, calls, sends, handlers: null }

  const client = {
    putKey: async (provider: string, key: string) => {
      calls.push(`putKey:${provider}=${key}`)
    },
    shutdown: async () => {
      calls.push('shutdown')
    }
  }

  const deps: ManagerDeps = {
    ensure: async () => {
      calls.push('ensure')
      if (opts.ensureFails) throw new Error('spawn failed: no uv')
      return handleFixture
    },
    createClient: () => {
      calls.push('createClient')
      return client as never
    },
    createStream: (_client, handlers) => {
      world.handlers = handlers
      return {
        run: async () => {
          calls.push('stream.run')
        },
        abort: () => {
          calls.push('stream.abort')
        }
      }
    },
    vault: {
      mode: 'encrypted',
      keys: () => opts.vaultKeys ?? {},
      setKey: (provider, key) => {
        calls.push(`vault.setKey:${provider}=${key}`)
      }
    },
    send: (channel, payload) => {
      calls.push(`send:${channel}`)
      sends.push({ channel, payload })
    },
    isAlive: () => false,
    killPid: () => {
      calls.push('killPid')
    },
    sleep: async () => {},
    log: () => {}
  }
  world.manager = new EngineManager(deps)
  return world
}

describe('EngineManager.start', () => {
  it('pushes all vault keys BEFORE broadcasting engine-ready (task 3.2 ordering)', async () => {
    const world = makeWorld({ vaultKeys: { anthropic: 'sk-1', openai: 'sk-2' } })
    await world.manager.start()
    const readyIndex = world.sends.findIndex(
      (s) =>
        s.channel === 'engine:status' && (s.payload as { state: string }).state === 'ready'
    )
    expect(readyIndex).toBeGreaterThanOrEqual(0)
    const pushIndexes = ['putKey:anthropic=sk-1', 'putKey:openai=sk-2'].map((c) =>
      world.calls.indexOf(c)
    )
    const readyCallIndex = world.calls.lastIndexOf('send:engine:status')
    for (const pushIndex of pushIndexes) {
      expect(pushIndex).toBeGreaterThanOrEqual(0)
      expect(pushIndex).toBeLessThan(readyCallIndex)
    }
  })

  it('broadcasts starting, then ready with the handle facts', async () => {
    const world = makeWorld()
    await world.manager.start()
    expect(world.sends[0]).toEqual({ channel: 'engine:status', payload: { state: 'starting' } })
    expect(world.sends.at(-1)).toEqual({
      channel: 'engine:status',
      payload: { state: 'ready', port: 50000, pid: 4242, version: '0.1.0', adopted: true }
    })
    expect(world.manager.status.state).toBe('ready')
  })

  it('broadcasts a failed status when supervision fails', async () => {
    const world = makeWorld({ ensureFails: true })
    await world.manager.start()
    expect(world.manager.status).toEqual({
      state: 'failed',
      message: expect.stringContaining('spawn failed')
    })
    expect(world.sends.at(-1)?.payload).toMatchObject({ state: 'failed' })
  })

  it('forwards stream events and hydrations to the renderer', async () => {
    const world = makeWorld()
    await world.manager.start()
    world.handlers?.onEvent({ kind: 'warning', step: null, message: 'm', data: {} })
    world.handlers?.onHydrate([])
    expect(world.sends.map((s) => s.channel)).toContain('engine:event')
    expect(world.sends.map((s) => s.channel)).toContain('jobs:hydrated')
  })
})

describe('EngineManager.putKey', () => {
  it('stores in the vault, then pushes to the engine (clear pushes "")', async () => {
    const world = makeWorld()
    await world.manager.start()
    await world.manager.putKey('anthropic', 'sk-new')
    await world.manager.putKey('anthropic', '')
    expect(world.calls).toContain('vault.setKey:anthropic=sk-new')
    expect(world.calls).toContain('putKey:anthropic=sk-new')
    expect(world.calls).toContain('vault.setKey:anthropic=')
    expect(world.calls).toContain('putKey:anthropic=')
    expect(world.calls.indexOf('vault.setKey:anthropic=sk-new')).toBeLessThan(
      world.calls.indexOf('putKey:anthropic=sk-new')
    )
  })
})

describe('EngineManager.quit', () => {
  it('aborts the stream, posts shutdown, and reports stopped (per P1)', async () => {
    const world = makeWorld()
    await world.manager.start()
    world.calls.length = 0
    await world.manager.quit({ timeoutMs: 5 })
    expect(world.calls.indexOf('stream.abort')).toBeLessThan(world.calls.indexOf('shutdown'))
    expect(world.manager.status.state).toBe('stopped')
  })

  it('is a no-op before start', async () => {
    const world = makeWorld()
    await world.manager.quit({ timeoutMs: 5 })
    expect(world.calls).toEqual([])
  })
})
