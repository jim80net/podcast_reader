import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { describe, expect, it, vi } from 'vitest'

import { tokenFingerprint } from './discovery'
import { ensureEngine, EngineStartupError } from './engine'
import type { EngineChildLike, SupervisorDeps } from './engine'

const TOKEN = 'test-token'
const FP = tokenFingerprint(TOKEN)
const DATA_DIR = '/data'

class FakeChild extends EventEmitter implements EngineChildLike {
  pid = 7777
  stdout = new PassThrough()
  stderr = new PassThrough()
  killed: string[] = []

  kill(signal?: NodeJS.Signals): boolean {
    this.killed.push(signal ?? 'SIGTERM')
    return true
  }
}

interface World {
  deps: SupervisorDeps
  files: Map<string, string>
  spawns: { argv: string[]; env: Record<string, string | undefined>; cwd?: string }[]
  killedPids: number[]
  shutdownPosts: string[]
  child: FakeChild
}

/**
 * A scripted engine world. Health responses are derived from the CURRENT
 * discovery file (as `write_discovery` derives them from the real engine),
 * so a respawn that rewrites the file is observed naturally. The stale
 * engine's `staleHealthStatus` override applies only while its PID is alive.
 */
function makeWorld(opts: {
  discovery?: object | string
  pidAlive?: boolean
  staleHealthStatus?: number
  childBehavior?: 'ready' | 'exit' | 'silent' | 'spawn-error'
  pidDiesAfterShutdownPost?: boolean
  shutdownStatus?: number
}): World {
  const files = new Map<string, string>()
  if (opts.discovery !== undefined) {
    files.set(
      `${DATA_DIR}/engine.json`,
      typeof opts.discovery === 'string' ? opts.discovery : JSON.stringify(opts.discovery)
    )
  }
  files.set(
    `${DATA_DIR}/engine-state.json`,
    JSON.stringify({ port: 50000, token: TOKEN })
  )

  const child = new FakeChild()
  const spawns: World['spawns'] = []
  const killedPids: number[] = []
  const shutdownPosts: string[] = []
  let stalePidAlive = opts.pidAlive ?? true

  const fetchFn = (async (input: Parameters<typeof fetch>[0], init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/v1/shutdown') && init?.method === 'POST') {
      shutdownPosts.push(url)
      if (opts.pidDiesAfterShutdownPost) stalePidAlive = false
      return new Response(null, { status: opts.shutdownStatus ?? 202 })
    }
    if (url.endsWith('/v1/health')) {
      if (opts.staleHealthStatus !== undefined && stalePidAlive) {
        return new Response('{"detail":"unauthorized"}', { status: opts.staleHealthStatus })
      }
      const discText = files.get(`${DATA_DIR}/engine.json`)
      const disc = discText !== undefined ? (JSON.parse(discText) as Record<string, unknown>) : {}
      return new Response(
        JSON.stringify({
          version: disc['version'] ?? '0.3.0',
          token_fingerprint: disc['token_fingerprint'] ?? FP
        }),
        { status: 200 }
      )
    }
    return new Response('{}', { status: 404 })
  }) as typeof fetch

  const deps: SupervisorDeps = {
    dataDir: DATA_DIR,
    env: { PATH: '/usr/bin' },
    resourcesPath: null,
    devCwd: '/repo',
    platform: 'linux',
    spawnFn: (argv, spawnOpts) => {
      spawns.push({ argv, env: spawnOpts.env, cwd: spawnOpts.cwd })
      queueMicrotask(() => {
        if (opts.childBehavior === 'exit') {
          child.stderr.write('Traceback: engine exploded\n')
          child.emit('exit', 1, null)
        } else if (opts.childBehavior === 'spawn-error') {
          // spawn(2) failure: child_process emits an async 'error' event and
          // 'exit' never fires (there is no process to exit).
          child.emit('error', Object.assign(new Error('spawn uv ENOENT'), { code: 'ENOENT' }))
        } else if (opts.childBehavior !== 'silent') {
          // the engine writes discovery (its own pid) strictly before the sentinel
          files.set(
            `${DATA_DIR}/engine.json`,
            JSON.stringify({ port: 50000, pid: child.pid, token_fingerprint: FP, version: '0.3.0' })
          )
          child.stdout.write('PODCAST_READER_READY\n')
        }
      })
      return child
    },
    fetchFn,
    fileExists: (p) => files.has(p),
    readFile: async (p) => {
      const content = files.get(p)
      if (content === undefined) throw Object.assign(new Error(`ENOENT: ${p}`), { code: 'ENOENT' })
      return content
    },
    isAlive: (pid) => (pid === child.pid ? true : stalePidAlive),
    killPid: (pid) => {
      killedPids.push(pid)
      stalePidAlive = false
    },
    sleep: async () => {},
    readinessTimeoutMs: 50,
    staleStopTimeoutMs: 5,
    log: () => {}
  }
  return { deps, files, spawns, killedPids, shutdownPosts, child }
}

const liveDiscovery = { port: 50000, pid: 4242, token_fingerprint: FP, version: '0.3.0' }

describe('ensureEngine — adopt', () => {
  it('adopts a live, healthy, version-sufficient engine without spawning', async () => {
    const world = makeWorld({ discovery: liveDiscovery })
    const handle = await ensureEngine(world.deps)
    expect(handle).toMatchObject({ adopted: true, pid: 4242, port: 50000, token: TOKEN })
    expect(world.spawns).toEqual([])
    expect(world.killedPids).toEqual([])
  })

  it('adopts an engine reporting a NEWER version (per P3/Q1)', async () => {
    const world = makeWorld({ discovery: { ...liveDiscovery, version: '99.0.0' } })
    const handle = await ensureEngine(world.deps)
    expect(handle.adopted).toBe(true)
    expect(handle.version).toBe('99.0.0')
  })

  it('spawns when there is no discovery file', async () => {
    const world = makeWorld({})
    const handle = await ensureEngine(world.deps)
    expect(handle.adopted).toBe(false)
    expect(world.spawns).toHaveLength(1)
  })

  it('spawns when the discovered PID is dead, without killing anything', async () => {
    const world = makeWorld({ discovery: liveDiscovery, pidAlive: false })
    const handle = await ensureEngine(world.deps)
    expect(handle.adopted).toBe(false)
    expect(world.killedPids).toEqual([])
    expect(world.spawns).toHaveLength(1)
  })

  it('kills and respawns on token-fingerprint mismatch', async () => {
    const world = makeWorld({
      discovery: { ...liveDiscovery, token_fingerprint: 'beefbeefbeefbeef' }
    })
    const handle = await ensureEngine(world.deps)
    expect(world.killedPids).toEqual([4242])
    expect(handle.adopted).toBe(false)
  })

  it('kills and respawns when health is unauthorized', async () => {
    const world = makeWorld({ discovery: liveDiscovery, staleHealthStatus: 401 })
    const handle = await ensureEngine(world.deps)
    expect(world.killedPids).toEqual([4242])
    expect(handle.adopted).toBe(false)
  })

  it('gracefully stops an engine older than MIN_ENGINE_VERSION, then respawns (per P3/Q1)', async () => {
    // 0.1.0 is the Phase 1/2 engine version — it lacks /v1/shutdown,
    // /v1/providers, /v1/keys/test, and confirm/discard, so it sits below
    // the 0.3.0 floor and takes the kill path.
    const world = makeWorld({
      discovery: { ...liveDiscovery, version: '0.1.0' },
      pidDiesAfterShutdownPost: true
    })
    const handle = await ensureEngine(world.deps)
    // it answered health, so it gets the graceful POST /v1/shutdown, not a force-kill
    expect(world.shutdownPosts).toHaveLength(1)
    expect(world.killedPids).toEqual([])
    expect(handle.adopted).toBe(false)
    expect(world.spawns).toHaveLength(1)
  })

  it('skips the graceful wait and force-kills when the shutdown POST is rejected (non-202)', async () => {
    // The stale pid dies right after the rejected POST — if the supervisor
    // wrongly credited a non-202 response as an accepted shutdown, it would
    // see that exit during the graceful wait and skip the force-kill. Only
    // a 202 (the documented 202-then-exit contract) earns the wait.
    const world = makeWorld({
      discovery: { ...liveDiscovery, version: '0.1.0' },
      shutdownStatus: 503,
      pidDiesAfterShutdownPost: true
    })
    const handle = await ensureEngine(world.deps)
    expect(world.shutdownPosts).toHaveLength(1)
    expect(world.killedPids).toEqual([4242])
    expect(handle.adopted).toBe(false)
  })

  it('force-kills an old engine that ignores graceful shutdown', async () => {
    const world = makeWorld({
      discovery: { ...liveDiscovery, version: '0.1.0' },
      pidDiesAfterShutdownPost: false
    })
    const handle = await ensureEngine(world.deps)
    expect(world.shutdownPosts).toHaveLength(1)
    expect(world.killedPids).toEqual([4242])
    expect(handle.adopted).toBe(false)
  })
})

describe('ensureEngine — spawn', () => {
  it('completes the sentinel-then-discovery handshake against the spawned engine', async () => {
    const world = makeWorld({})
    const handle = await ensureEngine(world.deps)
    expect(handle).toMatchObject({
      adopted: false,
      pid: 7777,
      port: 50000,
      token: TOKEN,
      version: '0.3.0'
    })
    expect(handle.child).not.toBeNull()
  })

  it('uses the dev fallback command and the dev cwd', async () => {
    const world = makeWorld({})
    await ensureEngine(world.deps)
    expect(world.spawns[0]?.argv).toEqual(['uv', 'run', 'podcast-reader', 'serve'])
    expect(world.spawns[0]?.cwd).toBe('/repo')
  })

  it('passes the resolved data dir to the child (per P9)', async () => {
    const world = makeWorld({})
    await ensureEngine(world.deps)
    expect(world.spawns[0]?.env['PODCAST_READER_DATA_DIR']).toBe(DATA_DIR)
  })

  it('honors the PODCAST_READER_ENGINE_CMD whitespace split (per P6)', async () => {
    const world = makeWorld({})
    world.deps.env['PODCAST_READER_ENGINE_CMD'] = 'python -m podcast_reader serve'
    await ensureEngine(world.deps)
    expect(world.spawns[0]?.argv).toEqual(['python', '-m', 'podcast_reader', 'serve'])
  })

  it('surfaces captured stderr when the child exits before the sentinel', async () => {
    const world = makeWorld({ childBehavior: 'exit' })
    const err = await ensureEngine(world.deps).then(
      () => null,
      (e: unknown) => e as EngineStartupError
    )
    expect(err).toBeInstanceOf(EngineStartupError)
    expect(err?.stderr).toContain('engine exploded')
  })

  it('rejects promptly with EngineStartupError when spawn itself fails (ENOENT)', async () => {
    const world = makeWorld({ childBehavior: 'spawn-error' })
    // A generous readiness timeout: if the rejection only came from the
    // sentinel timeout, the message would say so (and arrive late).
    world.deps.readinessTimeoutMs = 5000
    const err = await ensureEngine(world.deps).then(
      () => null,
      (e: unknown) => e as EngineStartupError
    )
    expect(err).toBeInstanceOf(EngineStartupError)
    expect(err?.message).toContain('ENOENT')
    expect(err?.message).not.toMatch(/sentinel/i)
  })

  it('fails with a timeout error when no sentinel ever appears', async () => {
    const world = makeWorld({ childBehavior: 'silent' })
    await expect(ensureEngine(world.deps)).rejects.toThrowError(/sentinel/i)
    expect(world.child.killed.length).toBeGreaterThan(0)
  })
})

describe('defaultSupervisorDeps', () => {
  it('killPid refuses pid <= 0 (kill(0)/kill(-n) address process groups, not a stale engine)', async () => {
    const { defaultSupervisorDeps } = await import('./engine')
    const deps = defaultSupervisorDeps({
      dataDir: '/tmp/x',
      env: {},
      resourcesPath: null,
      devCwd: null,
      log: () => {}
    })
    const spy = vi.spyOn(process, 'kill').mockImplementation(() => true)
    try {
      deps.killPid(0)
      deps.killPid(-7)
      expect(spy).not.toHaveBeenCalled()
    } finally {
      spy.mockRestore()
    }
  })
})
