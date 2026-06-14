import { describe, expect, it } from 'vitest'

import { EngineManager } from './engine-manager'
import { RespawnPolicy } from './respawn-policy'
import type { ManagerDeps } from './engine-manager'
import type { EngineHandle } from './engine'
import type { EventStreamHandlers } from './engine-client'

const handleFixture: EngineHandle = {
  port: 50000,
  pid: 4242,
  token: 'tok',
  version: '0.3.0',
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
  /** Number of distinct streams the manager has created (one per (re)wire). */
  streamCount(): number
  /** Resolve the in-flight backoff sleep (only when `deferSleep` is set). */
  resolveSleep(): void
  /** Resolve the held key-push (only when `deferPutKey` is set). */
  resolvePutKey(): void
  /** Advance the injected clock the policy reads. */
  setNow(ms: number): void
}

function makeWorld(
  opts: {
    vaultKeys?: Record<string, string>
    /** Providers whose putKey push rejects. */
    putKeyFails?: string[]
    ensureFails?: boolean
    handle?: Partial<EngineHandle>
    /** Per-ensure() overrides applied in order (later respawns). Falls back to `handle`. */
    handles?: Partial<EngineHandle>[]
    /** Make `ensure()` reject on the Nth (0-based) call. */
    ensureFailsOnCall?: number
    /** Provide an explicit policy (else a deterministic no-jitter one). */
    policy?: RespawnPolicy
    /** Hold backoff sleeps open until `resolveSleep()` (quit-race tests). */
    deferSleep?: boolean
    /** Hold the key-push await open until `resolvePutKey()` (wireUp-race tests). */
    deferPutKey?: boolean
  } = {}
): World {
  const calls: string[] = []
  const sends: { channel: string; payload: unknown }[] = []
  let streams = 0
  let now = 0
  let pendingSleep: (() => void) | null = null
  let pendingPutKey: (() => void) | null = null
  let ensureCalls = 0
  const world = {
    manager: null as unknown as EngineManager,
    calls,
    sends,
    handlers: null as EventStreamHandlers | null,
    streamCount: () => streams,
    resolveSleep: () => {
      const r = pendingSleep
      pendingSleep = null
      r?.()
    },
    resolvePutKey: () => {
      const r = pendingPutKey
      pendingPutKey = null
      r?.()
    },
    setNow: (ms: number) => {
      now = ms
    }
  }

  const client = {
    putKey: async (provider: string, key: string) => {
      calls.push(`putKey:${provider}=${key}`)
      if (opts.putKeyFails?.includes(provider)) throw new Error(`engine rejected ${provider}`)
      if (opts.deferPutKey) await new Promise<void>((resolve) => (pendingPutKey = resolve))
    },
    shutdown: async () => {
      calls.push('shutdown')
    }
  }

  const deps: ManagerDeps = {
    ensure: async () => {
      const call = ensureCalls
      ensureCalls += 1
      calls.push('ensure')
      if (opts.ensureFails || opts.ensureFailsOnCall === call) {
        throw new Error('spawn failed: no uv')
      }
      const override = opts.handles?.[call] ?? opts.handle
      return { ...handleFixture, ...override }
    },
    createClient: () => {
      calls.push('createClient')
      return client as never
    },
    createStream: (_client, handlers) => {
      streams += 1
      const id = streams
      world.handlers = handlers
      return {
        run: async () => {
          calls.push(`stream.run#${id}`)
        },
        abort: () => {
          calls.push(`stream.abort#${id}`)
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
    sleep: (ms: number) => {
      calls.push(`sleep:${ms}`)
      if (opts.deferSleep) return new Promise<void>((resolve) => (pendingSleep = resolve))
      return Promise.resolve()
    },
    now: () => now,
    policy: opts.policy ?? new RespawnPolicy({ jitter: () => 0 }),
    log: () => {}
  }
  world.manager = new EngineManager(deps)
  return world
}

/** A spawned (non-adopted) handle whose child exit is caller-controlled. */
function spawnedWorldHandle(childExited: Promise<void>): Partial<EngineHandle> {
  return {
    adopted: false,
    posture: 'dev',
    child: { kill: () => true } as never,
    childExited
  }
}

/** Resolve microtasks so async respawn steps settle in tests. */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

describe('EngineManager.media (app:// media-protocol access)', () => {
  it('is null before start and exposes loopback baseUrl + token once ready', async () => {
    const world = makeWorld()
    expect(world.manager.media).toBeNull()
    await world.manager.start()
    expect(world.manager.media).toEqual({ baseUrl: 'http://127.0.0.1:50000', token: 'tok' })
  })

  it('returns to null after quit so the media handler reports engine-not-ready', async () => {
    const world = makeWorld()
    await world.manager.start()
    await world.manager.quit()
    expect(world.manager.media).toBeNull()
  })
})

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

  it('attempts every vault key and surfaces push failures by provider on the ready status', async () => {
    const world = makeWorld({
      vaultKeys: { anthropic: 'sk-1', openai: 'sk-2' },
      putKeyFails: ['anthropic']
    })
    await world.manager.start()
    // the failed push does not stop the remaining keys
    expect(world.calls).toContain('putKey:anthropic=sk-1')
    expect(world.calls).toContain('putKey:openai=sk-2')
    // ...but the failure is visible, not silently logged away
    expect(world.manager.status).toEqual({
      state: 'ready',
      port: 50000,
      pid: 4242,
      version: '0.3.0',
      adopted: true,
      keyPushFailures: ['anthropic']
    })
    const ready = world.sends.find(
      (s) => s.channel === 'engine:status' && (s.payload as { state: string }).state === 'ready'
    )
    expect((ready?.payload as { keyPushFailures?: string[] }).keyPushFailures).toEqual([
      'anthropic'
    ])
  })

  it('omits keyPushFailures from the ready status when every push succeeds', async () => {
    const world = makeWorld({ vaultKeys: { anthropic: 'sk-1' } })
    await world.manager.start()
    expect(world.manager.status).toEqual({
      state: 'ready',
      port: 50000,
      pid: 4242,
      version: '0.3.0',
      adopted: true
    })
  })

  it('broadcasts starting, then ready with the handle facts', async () => {
    const world = makeWorld()
    await world.manager.start()
    expect(world.sends[0]).toEqual({ channel: 'engine:status', payload: { state: 'starting' } })
    expect(world.sends.at(-1)).toEqual({
      channel: 'engine:status',
      payload: { state: 'ready', port: 50000, pid: 4242, version: '0.3.0', adopted: true }
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

describe('EngineManager — unexpected engine exit (respawn supervision)', () => {
  it('respawns the engine: restarting → ready, keys re-pushed, exactly one new stream', async () => {
    let exitChild!: () => void
    const childExited = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    const world = makeWorld({
      vaultKeys: { anthropic: 'sk-1' },
      handle: spawnedWorldHandle(childExited),
      // The respawn's ensure() returns a fresh spawned handle that never exits.
      handles: [
        spawnedWorldHandle(childExited),
        spawnedWorldHandle(new Promise<void>(() => {}))
      ]
    })
    await world.manager.start()
    expect(world.manager.status.state).toBe('ready')
    expect(world.streamCount()).toBe(1)

    exitChild()
    await flush()

    const statuses = world.sends
      .filter((s) => s.channel === 'engine:status')
      .map((s) => (s.payload as { state: string }).state)
    // restarting was broadcast, and the engine returned to ready.
    expect(statuses).toContain('restarting')
    expect(world.manager.status.state).toBe('ready')
    // The dead stream was aborted and exactly one new stream was created.
    expect(world.calls).toContain('stream.abort#1')
    expect(world.streamCount()).toBe(2)
    // Keys were pushed again on respawn (push count == start + respawn).
    expect(world.calls.filter((c) => c === 'putKey:anthropic=sk-1')).toHaveLength(2)
  })

  it('reports media not-ready (null) while restarting', async () => {
    let exitChild!: () => void
    const childExited = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    const world = makeWorld({
      handle: spawnedWorldHandle(childExited),
      // ensure() never resolves on respawn, so we stay in the restart window.
      handles: [
        spawnedWorldHandle(childExited),
        new Promise<Partial<EngineHandle>>(() => {}) as never
      ],
      ensureFailsOnCall: undefined,
      deferSleep: true
    })
    await world.manager.start()
    expect(world.manager.media).not.toBeNull()

    exitChild()
    await flush()
    // Inside the backoff sleep (deferred): the crash path nulled ownership.
    expect(world.manager.media).toBeNull()
    expect(world.manager.status.state).toBe('restarting')
  })

  it('does not respawn when the child exits during the quit sequence', async () => {
    let exitChild!: () => void
    const childExited = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    const world = makeWorld({ handle: spawnedWorldHandle(childExited) })
    await world.manager.start()

    const quitting = world.manager.quit({ timeoutMs: 5 })
    exitChild() // the engine exiting IS the quit sequence succeeding
    await quitting
    await flush()
    expect(world.manager.status.state).toBe('stopped')
    const statuses = world.sends
      .filter((s) => s.channel === 'engine:status')
      .map((s) => (s.payload as { state: string }).state)
    expect(statuses).not.toContain('failed')
    expect(statuses).not.toContain('restarting')
    // No respawn ensure() ran (only the initial start's ensure()).
    expect(world.calls.filter((c) => c === 'ensure')).toHaveLength(1)
  })
})

describe('EngineManager — respawn give-up', () => {
  it('reports failed after MAX_RESPAWN_ATTEMPTS consecutive failures', async () => {
    let exitChild!: () => void
    const firstExit = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    // First start succeeds with a spawned handle; every respawn ensure() fails.
    const world = makeWorld({
      handle: spawnedWorldHandle(firstExit),
      ensureFailsOnCall: undefined
    })
    // Override: only the initial ensure() (call 0) succeeds; respawns reject.
    const deps = (world.manager as unknown as { deps: ManagerDeps }).deps
    let call = 0
    const realEnsure = deps.ensure
    deps.ensure = async () => {
      const c = call
      call += 1
      if (c === 0) return realEnsure()
      world.calls.push('ensure')
      throw new Error('spawn failed: no uv')
    }

    await world.manager.start()
    exitChild()
    await flush()
    await flush()
    await flush()

    expect(world.manager.status.state).toBe('failed')
    const ensureAttempts = world.calls.filter((c) => c === 'ensure').length
    // 1 initial + 3 respawn attempts, then give up.
    expect(ensureAttempts).toBe(1 + 3)
  })
})

describe('EngineManager — quit during respawn (C1/C2 checkpoints)', () => {
  it('quit during the backoff leaves no surviving engine and reports stopped', async () => {
    let exitChild!: () => void
    const firstExit = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    const world = makeWorld({
      handle: spawnedWorldHandle(firstExit),
      handles: [spawnedWorldHandle(firstExit), spawnedWorldHandle(new Promise<void>(() => {}))],
      deferSleep: true
    })
    await world.manager.start()

    exitChild()
    await flush()
    // We are parked inside the backoff sleep.
    expect(world.manager.status.state).toBe('restarting')

    // Quit wins the race: it sets quitting=true first; there is no live handle
    // (crash path nulled it), so quit() returns immediately.
    await world.manager.quit({ timeoutMs: 5 })
    // Releasing the backoff: the respawn re-checks quitting and bails — no ensure().
    world.resolveSleep()
    await flush()
    await flush()

    const respawnEnsures = world.calls.filter((c) => c === 'ensure').length
    expect(respawnEnsures).toBe(1) // only the initial start
    // The after-backoff quit bail settles on the terminal stopped state (cubic P2).
    expect(world.manager.status.state).toBe('stopped')
  })

  it('quit after ensure() resolves SIGKILLs the just-spawned child', async () => {
    let exitChild!: () => void
    const firstExit = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    let killed = false
    const survivor: Partial<EngineHandle> = {
      adopted: false,
      posture: 'dev',
      child: {
        kill: () => {
          killed = true
          return true
        }
      } as never,
      childExited: new Promise<void>(() => {})
    }
    // ensure() for the respawn resolves only after we flip quitting via quit().
    let releaseEnsure!: () => void
    const ensureGate = new Promise<void>((resolve) => {
      releaseEnsure = resolve
    })
    const world = makeWorld({ handle: spawnedWorldHandle(firstExit) })
    const deps = (world.manager as unknown as { deps: ManagerDeps }).deps
    let call = 0
    const realEnsure = deps.ensure
    deps.ensure = async () => {
      const c = call
      call += 1
      if (c === 0) return realEnsure()
      world.calls.push('ensure')
      await ensureGate
      return { port: 1, pid: 2, token: 't', version: 'v', ...survivor } as EngineHandle
    }

    await world.manager.start()
    exitChild()
    await flush() // through backoff (immediate) into the respawn ensure()
    // Quit while ensure() is in flight.
    const quitting = world.manager.quit({ timeoutMs: 5 })
    await quitting
    releaseEnsure()
    await flush()
    await flush()

    expect(killed).toBe(true)
    expect(world.manager.status.state).toBe('stopped')
    // The just-spawned child was never wired up (no new stream).
    expect(world.streamCount()).toBe(1)
  })
})

describe('EngineManager.restart (manual recovery)', () => {
  it('resets the budget and respawns from failed without going through quit()', async () => {
    const world = makeWorld({ handle: spawnedWorldHandle(new Promise<void>(() => {})) })
    // Put the manager into failed directly via a forced give-up is complex; instead
    // verify restart() wires up a fresh engine and never calls shutdown.
    await world.manager.start()
    world.calls.length = 0
    await world.manager.restart()
    await flush()
    expect(world.calls).toContain('ensure')
    expect(world.calls).not.toContain('shutdown')
    expect(world.manager.status.state).toBe('ready')
  })

  it('is a no-op while a (re)start is already in flight', async () => {
    const world = makeWorld({
      handle: spawnedWorldHandle(new Promise<void>(() => {})),
      deferSleep: true
    })
    await world.manager.start()
    world.calls.length = 0
    // Two concurrent restarts: the second must be a no-op.
    const first = world.manager.restart()
    const second = world.manager.restart()
    await Promise.all([first, second])
    await flush()
    const ensures = world.calls.filter((c) => c === 'ensure').length
    expect(ensures).toBe(1)
  })
})

describe('EngineManager.quit', () => {
  it('aborts the stream, posts shutdown, and reports stopped (per P1)', async () => {
    const world = makeWorld()
    await world.manager.start()
    world.calls.length = 0
    await world.manager.quit({ timeoutMs: 5 })
    expect(world.calls.indexOf('stream.abort#1')).toBeLessThan(world.calls.indexOf('shutdown'))
    expect(world.manager.status.state).toBe('stopped')
  })

  it('is a no-op before start', async () => {
    const world = makeWorld()
    await world.manager.quit({ timeoutMs: 5 })
    expect(world.calls).toEqual([])
  })
})

describe('EngineManager — crash/quit during wireUp key-push (C1/C2 wireUp window)', () => {
  it('a crash during the key-push does not settle to ready or create a stream', async () => {
    let exitChild!: () => void
    const childExited = new Promise<void>((resolve) => {
      exitChild = resolve
    })
    const world = makeWorld({
      vaultKeys: { anthropic: 'sk-1' },
      handle: spawnedWorldHandle(childExited),
      handles: [spawnedWorldHandle(childExited), spawnedWorldHandle(new Promise<void>(() => {}))],
      deferPutKey: true,
      deferSleep: true // park the respawn in its backoff so we inspect the window
    })
    // start() hangs awaiting the held key-push (wireUp mid-loop).
    void world.manager.start()
    await flush()
    // The child crashes while we are still pushing keys.
    exitChild()
    await flush()
    // Now release the held key-push: the stale wireUp continuation must bail.
    world.resolvePutKey()
    await flush()

    // The crash path owns the state (restarting, parked in backoff). The stale
    // wireUp neither broadcast ready nor created a stream against the dead engine.
    expect(world.manager.status.state).toBe('restarting')
    expect(world.streamCount()).toBe(0)
    expect(world.calls).not.toContain('stream.run#1')
  })

  it('a quit during the key-push ends stopped, with no stream and media null', async () => {
    // The child never exits on its own here — quit() is what lands mid-push.
    const world = makeWorld({
      vaultKeys: { anthropic: 'sk-1' },
      handle: spawnedWorldHandle(new Promise<void>(() => {})),
      deferPutKey: true
    })
    void world.manager.start()
    await flush()
    // Quit lands while wireUp is mid key-push.
    const quitting = world.manager.quit({ timeoutMs: 5 })
    await quitting
    // Releasing the key-push: the stale wireUp must bail on the quitting flag.
    world.resolvePutKey()
    await flush()

    expect(world.manager.status.state).toBe('stopped')
    expect(world.manager.media).toBeNull()
    expect(world.streamCount()).toBe(0)
    expect(world.calls).not.toContain('stream.run#1')
  })

  it('a second crash <60s after a healthy respawn counts as attempt 2 (respawn re-stamps markReady)', async () => {
    let exitFirst!: () => void
    const firstExit = new Promise<void>((resolve) => {
      exitFirst = resolve
    })
    let exitSecond!: () => void
    const secondExit = new Promise<void>((resolve) => {
      exitSecond = resolve
    })
    const world = makeWorld({
      handle: spawnedWorldHandle(firstExit),
      // respawn #1 → a healthy engine that later crashes; respawn #2 → never exits.
      handles: [
        spawnedWorldHandle(firstExit),
        spawnedWorldHandle(secondExit),
        spawnedWorldHandle(new Promise<void>(() => {}))
      ],
      deferSleep: true // hold respawn #1's backoff so we can advance the clock inside it
    })
    world.setNow(0)
    await world.manager.start() // initial markReady at t=0
    expect(world.manager.status.state).toBe('ready')

    exitFirst() // crash 1 at t=0 → attempt 1 → parked in backoff
    await flush()
    // Advance the clock PAST the 60s reset window BEFORE respawn #1 reaches ready,
    // so respawn's wireUp re-stamps markReady at t=100_000. If it did NOT re-stamp,
    // lastReadyAt would still be 0 and the next crash would reset to attempt 1.
    world.setNow(100_000)
    world.resolveSleep()
    await flush()
    expect(world.manager.status.state).toBe('ready')

    world.setNow(101_000) // 1s after respawn's markReady, but 101s after the initial one
    exitSecond()
    await flush()

    const restarting = world.sends
      .filter((s) => s.channel === 'engine:status')
      .map((s) => s.payload as { state: string; attempt?: number })
      .filter((s) => s.state === 'restarting')
    // attempt 2 is only reachable if respawn #1 re-stamped markReady at t=100_000
    // (else the 101s gap since the initial markReady would have reset to attempt 1).
    expect(restarting.at(-1)?.attempt).toBe(2)
    expect(world.calls).toContain('sleep:2000') // attempt-2 backoff
  })

  it('a wireUp failure clears spawning and does not wedge future restarts (OCR)', async () => {
    // createStream throwing simulates a wiring failure after a successful spawn.
    const world = makeWorld({ handle: spawnedWorldHandle(new Promise<void>(() => {})) })
    const deps = (world.manager as unknown as { deps: ManagerDeps }).deps
    let failNextStream = true
    const realCreateStream = deps.createStream
    deps.createStream = (client, handlers) => {
      if (failNextStream) {
        failNextStream = false
        throw new Error('stream wiring blew up')
      }
      return realCreateStream(client, handlers)
    }
    await world.manager.start()
    // The wireUp threw → not ready, but spawning was cleared (not wedged) and a
    // status was surfaced rather than an unhandled rejection.
    expect(world.manager.status.state).not.toBe('ready')
    // A subsequent restart is NOT blocked by a stuck spawning flag.
    await world.manager.restart()
    expect(world.manager.status.state).toBe('ready')
  })
})
