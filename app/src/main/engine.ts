import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createInterface } from 'node:readline'

import { parseDiscovery, parseEngineState, tokenFingerprint } from './discovery'
import { resolveEngineCommand } from './engine-cmd'
import { pidIsAlive, waitForPidExit } from './quit'
import { MIN_ENGINE_VERSION, versionAtLeast } from './version'
import { DISCOVERY_FILE, ENGINE_STATE_FILE, READY_SENTINEL } from '../shared/types'
import type { EnginePosture } from './engine-cmd'
import type { DiscoveryInfo, HealthInfo } from '../shared/types'

/**
 * Engine supervision (design decision 2): the Phase 1 handshake mirrored in
 * the app's main process. Adopt-or-kill against the discovery file, the
 * three-way spawn chain, sentinel-then-discovery readiness, and never two
 * engines for one data dir.
 *
 * Everything reaches the OS through `SupervisorDeps`, so the whole flow is
 * unit-testable with a scripted fake child process; `defaultSupervisorDeps`
 * binds the real implementations.
 */

/** The subset of `child_process.ChildProcess` the supervisor relies on (test seam). */
export interface EngineChildLike {
  pid?: number | undefined
  stdout: NodeJS.ReadableStream | null
  stderr: NodeJS.ReadableStream | null
  kill(signal?: NodeJS.Signals): boolean
  once(event: 'exit', listener: (code: number | null, signal: NodeJS.Signals | null) => void): unknown
}

export interface SupervisorDeps {
  /** Resolved engine data dir (the app's own resolution; passed to the child per P9). */
  dataDir: string
  env: Record<string, string | undefined>
  /** `process.resourcesPath` when packaged, else null. */
  resourcesPath: string | null
  /** cwd for override/dev spawns (the repo root in development). */
  devCwd: string | null
  platform: NodeJS.Platform
  spawnFn: (
    argv: string[],
    opts: { env: Record<string, string | undefined>; cwd?: string }
  ) => EngineChildLike
  fetchFn: typeof fetch
  fileExists: (path: string) => boolean
  readFile: (path: string) => Promise<string>
  isAlive: (pid: number) => boolean
  /** Force-kill (SIGKILL / TerminateProcess) a non-child PID. */
  killPid: (pid: number) => void
  sleep: (ms: number) => Promise<void>
  readinessTimeoutMs?: number
  /** Bound on waiting for a stale engine to die before/after force-kill. */
  staleStopTimeoutMs?: number
  healthTimeoutMs?: number
  log: (message: string) => void
}

export interface EngineHandle {
  port: number
  pid: number
  token: string
  version: string
  adopted: boolean
  posture: EnginePosture | 'adopted'
  child: EngineChildLike | null
  /** Resolves when the spawned child exits (null for adopted engines — PID-poll those, per P7). */
  childExited: Promise<void> | null
}

export class EngineStartupError extends Error {
  constructor(
    message: string,
    readonly stderr: string = ''
  ) {
    super(stderr === '' ? message : `${message}\n--- engine stderr ---\n${stderr}`)
  }
}

const DEFAULT_READINESS_TIMEOUT_MS = 30_000
const DEFAULT_STALE_STOP_TIMEOUT_MS = 10_000

/** Production bindings for `SupervisorDeps` (everything but the paths/env context). */
export function defaultSupervisorDeps(
  base: Pick<SupervisorDeps, 'dataDir' | 'env' | 'resourcesPath' | 'devCwd' | 'log'>
): SupervisorDeps {
  return {
    ...base,
    platform: process.platform,
    spawnFn: (argv, opts) => {
      const [cmd, ...args] = argv
      if (cmd === undefined) throw new EngineStartupError('empty engine command')
      return spawn(cmd, args, {
        env: opts.env as NodeJS.ProcessEnv,
        cwd: opts.cwd,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true
      })
    },
    fetchFn: fetch,
    fileExists: existsSync,
    readFile: (path) => readFile(path, 'utf8'),
    isAlive: pidIsAlive,
    killPid: (pid) => {
      try {
        process.kill(pid, process.platform === 'win32' ? undefined : 'SIGKILL')
      } catch {
        // already gone
      }
    },
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms))
  }
}

/**
 * Adopt a live engine when the full Phase 1 contract holds — PID alive,
 * authed health, fingerprint matching both files, version >= the floor
 * (newer adopted, per P3/Q1) — otherwise stop whatever is stale and spawn
 * fresh through the three-way command chain.
 */
export async function ensureEngine(deps: SupervisorDeps): Promise<EngineHandle> {
  const adopted = await tryAdopt(deps)
  if (adopted !== null) return adopted
  return spawnEngine(deps)
}

async function tryAdopt(deps: SupervisorDeps): Promise<EngineHandle | null> {
  const discoveryPath = join(deps.dataDir, DISCOVERY_FILE)
  let discovery: DiscoveryInfo
  try {
    discovery = parseDiscovery(await deps.readFile(discoveryPath))
  } catch (err) {
    deps.log(`no usable discovery file (${String(err)}); spawning`)
    return null
  }
  if (!deps.isAlive(discovery.pid)) {
    deps.log(`discovered engine pid ${discovery.pid} is dead; spawning`)
    return null
  }

  let token: string
  try {
    token = parseEngineState(await deps.readFile(join(deps.dataDir, ENGINE_STATE_FILE))).token
  } catch (err) {
    deps.log(`engine-state.json unusable (${String(err)}); treating pid ${discovery.pid} as stale`)
    await stopStale(deps, discovery, null)
    return null
  }
  if (tokenFingerprint(token) !== discovery.token_fingerprint) {
    deps.log(`token fingerprint mismatch for pid ${discovery.pid}; treating as stale`)
    await stopStale(deps, discovery, null)
    return null
  }

  const health = await fetchHealth(deps, discovery.port, token)
  if (health === null) {
    deps.log(`engine pid ${discovery.pid} did not answer health; treating as stale`)
    await stopStale(deps, discovery, null)
    return null
  }
  if (health.token_fingerprint !== discovery.token_fingerprint) {
    deps.log(`health fingerprint mismatch for pid ${discovery.pid}; treating as stale`)
    await stopStale(deps, discovery, token)
    return null
  }
  if (!versionAtLeast(health.version, MIN_ENGINE_VERSION)) {
    deps.log(
      `engine version ${health.version} < ${MIN_ENGINE_VERSION}; stopping it gracefully (per P3/Q1)`
    )
    await stopStale(deps, discovery, token)
    return null
  }

  deps.log(`adopted engine pid ${discovery.pid} on port ${discovery.port} (v${health.version})`)
  return {
    port: discovery.port,
    pid: discovery.pid,
    token,
    version: health.version,
    adopted: true,
    posture: 'adopted',
    child: null,
    childExited: null
  }
}

/**
 * Stop a stale engine: graceful `POST /v1/shutdown` when we hold a token it
 * answers to, then force-kill if it lingers. Never removes the discovery
 * file — the next engine rewrites it.
 */
async function stopStale(
  deps: SupervisorDeps,
  discovery: DiscoveryInfo,
  token: string | null
): Promise<void> {
  const timeoutMs = deps.staleStopTimeoutMs ?? DEFAULT_STALE_STOP_TIMEOUT_MS
  const waitOpts = { timeoutMs, isAlive: deps.isAlive, sleep: deps.sleep }
  if (token !== null) {
    try {
      await deps.fetchFn(`http://127.0.0.1:${discovery.port}/v1/shutdown`, {
        method: 'POST',
        headers: { authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(deps.healthTimeoutMs ?? 3000)
      })
    } catch {
      // unresponsive: fall through to force-kill
    }
    if (await waitForPidExit(discovery.pid, waitOpts)) return
  }
  deps.killPid(discovery.pid)
  if (!(await waitForPidExit(discovery.pid, waitOpts))) {
    throw new EngineStartupError(
      `stale engine pid ${discovery.pid} survived force-kill; refusing to start a second engine`
    )
  }
}

async function spawnEngine(deps: SupervisorDeps): Promise<EngineHandle> {
  const { argv, posture } = resolveEngineCommand({
    resourcesPath: deps.resourcesPath,
    env: deps.env,
    fileExists: deps.fileExists,
    platform: deps.platform
  })
  deps.log(`spawning engine (${posture}): ${argv.join(' ')}`)
  const child = deps.spawnFn(argv, {
    // Per P9: the child resolves the same data dir the app resolved.
    env: { ...deps.env, PODCAST_READER_DATA_DIR: deps.dataDir },
    cwd: posture === 'packaged' ? undefined : (deps.devCwd ?? undefined)
  })
  const childExited = new Promise<void>((resolve) => {
    child.once('exit', () => resolve())
  })
  const stderr = captureStderr(child)

  await awaitSentinel(deps, child, childExited, stderr)

  // Sentinel seen: the discovery file is complete on disk (write_discovery
  // prints it strictly after the atomic write). Read it; no port polling.
  try {
    const discovery = parseDiscovery(await deps.readFile(join(deps.dataDir, DISCOVERY_FILE)))
    const token = parseEngineState(
      await deps.readFile(join(deps.dataDir, ENGINE_STATE_FILE))
    ).token
    if (tokenFingerprint(token) !== discovery.token_fingerprint) {
      throw new EngineStartupError('spawned engine discovery/token fingerprint mismatch')
    }
    const health = await fetchHealth(deps, discovery.port, token)
    if (health === null || health.token_fingerprint !== discovery.token_fingerprint) {
      throw new EngineStartupError('spawned engine failed the health verification')
    }
    if (!versionAtLeast(health.version, MIN_ENGINE_VERSION)) {
      throw new EngineStartupError(
        `spawned engine reports version ${health.version} < required ${MIN_ENGINE_VERSION} ` +
          `(posture: ${posture}) — check PODCAST_READER_ENGINE_CMD / the packaged engine`
      )
    }
    deps.log(`engine ready: pid ${discovery.pid}, port ${discovery.port}, v${health.version}`)
    return {
      port: discovery.port,
      pid: discovery.pid,
      token,
      version: health.version,
      adopted: false,
      posture,
      child,
      childExited
    }
  } catch (err) {
    child.kill('SIGKILL')
    if (err instanceof EngineStartupError) throw err
    throw new EngineStartupError(`engine handshake failed after sentinel: ${String(err)}`, stderr())
  }
}

/** Wait for the READY sentinel on stdout, racing child exit and the readiness timeout. */
async function awaitSentinel(
  deps: SupervisorDeps,
  child: EngineChildLike,
  childExited: Promise<void>,
  stderr: () => string
): Promise<void> {
  const timeoutMs = deps.readinessTimeoutMs ?? DEFAULT_READINESS_TIMEOUT_MS
  if (child.stdout === null) {
    child.kill('SIGKILL')
    throw new EngineStartupError('engine child has no stdout to watch for the ready sentinel')
  }
  const rl = createInterface({ input: child.stdout })
  let timer: NodeJS.Timeout | undefined
  try {
    const sentinel = new Promise<'ready'>((resolve) => {
      rl.on('line', (line) => {
        if (line.trim() === READY_SENTINEL) resolve('ready')
      })
    })
    const timeout = new Promise<'timeout'>((resolve) => {
      timer = setTimeout(() => resolve('timeout'), timeoutMs)
    })
    const outcome = await Promise.race([
      sentinel,
      childExited.then(() => 'exited' as const),
      timeout
    ])
    if (outcome === 'exited') {
      throw new EngineStartupError('engine exited before signaling readiness', stderr())
    }
    if (outcome === 'timeout') {
      child.kill('SIGKILL')
      throw new EngineStartupError(
        `engine produced no ready sentinel within ${timeoutMs}ms`,
        stderr()
      )
    }
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    rl.close()
  }
}

const STDERR_CAP = 8192

function captureStderr(child: EngineChildLike): () => string {
  let captured = ''
  child.stderr?.on('data', (chunk: Buffer | string) => {
    captured = (captured + String(chunk)).slice(-STDERR_CAP)
  })
  return () => captured
}

async function fetchHealth(
  deps: SupervisorDeps,
  port: number,
  token: string
): Promise<HealthInfo | null> {
  try {
    const res = await deps.fetchFn(`http://127.0.0.1:${port}/v1/health`, {
      headers: { authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(deps.healthTimeoutMs ?? 3000)
    })
    if (!res.ok) return null
    const body = (await res.json()) as Record<string, unknown>
    if (typeof body['version'] !== 'string' || typeof body['token_fingerprint'] !== 'string') {
      return null
    }
    return { version: body['version'], token_fingerprint: body['token_fingerprint'] }
  } catch {
    return null
  }
}
