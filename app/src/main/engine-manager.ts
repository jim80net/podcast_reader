import { runQuitSequence, waitForPidExit } from './quit'
import { MAX_RESPAWN_ATTEMPTS, RespawnPolicy } from './respawn-policy'
import { PUSH_CHANNELS } from '../shared/ipc'
import type { EngineHandle } from './engine'
import type { EngineAccess } from './media-protocol'
import type { EngineClient, EventStreamHandlers } from './engine-client'
import type { EngineStatus } from '../shared/ipc'
import type { KeyStorageMode } from './vault'

/**
 * Composition root for engine supervision in the main process: runs
 * `ensureEngine`, pushes vaulted keys (strictly before the renderer is told
 * the engine is ready — task 3.2), consumes the event stream, broadcasts
 * status/events over IPC push channels, and owns the quit sequence.
 *
 * Electron-free by construction (everything OS- or app-shaped is injected),
 * so the orchestration ordering is unit-testable.
 */

export interface VaultLike {
  readonly mode: KeyStorageMode
  keys(): Record<string, string>
  setKey(provider: string, key: string): void
}

export interface ManagerDeps {
  ensure(): Promise<EngineHandle>
  createClient(handle: EngineHandle): EngineClient
  createStream(
    client: EngineClient,
    handlers: EventStreamHandlers
  ): { run(): Promise<void>; abort(): void }
  vault: VaultLike
  /** Broadcast to all renderer windows (webContents.send). */
  send(channel: string, payload: unknown): void
  isAlive(pid: number): boolean
  killPid(pid: number): void
  sleep(ms: number): Promise<void>
  /** Monotonic-enough wall clock the respawn policy reads (injected for tests). */
  now(): number
  /** The bounded respawn policy (injected so the boundary is deterministic in tests). */
  policy: RespawnPolicy
  log(message: string): void
}

export class EngineManager {
  status: EngineStatus = { state: 'starting' }
  private handle: EngineHandle | null = null
  private engineClient: EngineClient | null = null
  private stream: { run(): Promise<void>; abort(): void } | null = null
  /**
   * Set true as the FIRST line of `quit()` and cleared by `restart()`. Every
   * respawn step re-checks it after each await so no engine is spawned (or left
   * running) once the quit sequence has begun (engine-respawn-supervision C1/C2).
   */
  private quitting = false
  /** Re-entrancy guard: a (re)spawn or manual restart is in flight. */
  private spawning = false
  /** Which respawn attempt the current burst is on (1..MAX); 0 between bursts. */
  private attemptNumber = 0

  constructor(private readonly deps: ManagerDeps) {}

  /** The engine client, for IPC handlers. Null until the engine is ready. */
  get client(): EngineClient | null {
    return this.engineClient
  }

  /** The connected engine's port (pairing display needs it). Null until ready. */
  get port(): number | null {
    return this.handle?.port ?? null
  }

  get keyStorageMode(): KeyStorageMode {
    return this.deps.vault.mode
  }

  /**
   * Loopback engine coordinates for the `app://media` protocol handler
   * (media-protocol.ts): the bearer token the renderer never holds, plus the
   * 127.0.0.1 base URL. Null until ready and after quit, so the handler
   * answers 503 outside the engine's live window.
   */
  get media(): EngineAccess | null {
    if (this.handle === null || this.engineClient === null) return null
    return { baseUrl: `http://127.0.0.1:${this.handle.port}`, token: this.handle.token }
  }

  async start(): Promise<void> {
    this.setStatus({ state: 'starting' })
    this.spawning = true
    let handle: EngineHandle
    try {
      handle = await this.deps.ensure()
    } catch (err) {
      this.spawning = false
      this.setStatus({ state: 'failed', message: err instanceof Error ? err.message : String(err) })
      return
    }
    await this.wireUp(handle)
  }

  /**
   * Reconstruct the full live state for `handle`: watch for unexpected exit,
   * create the client, push every vaulted key (strictly before broadcasting
   * `ready` — design decision 5), stamp the healthy clock, broadcast `ready`,
   * then abort any prior stream and start a fresh one. Shared by `start()`,
   * the respawn loop, and manual `restart()` so a respawn reconstructs exactly
   * the same state a cold start does.
   */
  private async wireUp(handle: EngineHandle): Promise<void> {
    this.handle = handle
    this.observeChildExit(handle)
    const client = this.deps.createClient(handle)
    this.engineClient = client

    // Push-at-engine-start (design decision 5): every vaulted key reaches
    // engine memory BEFORE the renderer hears "ready" — no job submitted
    // after readiness can miss its key. A failed push must stay visible
    // (the vaulted key silently never reaching the engine looks like a
    // missing key at job time), so failures ride on the ready status.
    const keyPushFailures: string[] = []
    for (const [provider, key] of Object.entries(this.deps.vault.keys())) {
      try {
        await client.putKey(provider, key)
      } catch (err) {
        this.deps.log(`key push for ${provider} failed: ${String(err)}`)
        keyPushFailures.push(provider)
      }
    }

    // Verified-ready point: stamp the healthy clock so every (re)spawn that
    // reaches ready resets the failure budget (not just the first start).
    this.deps.policy.markReady(this.deps.now())
    this.attemptNumber = 0
    this.spawning = false

    this.setStatus({
      state: 'ready',
      port: handle.port,
      pid: handle.pid,
      version: handle.version,
      adopted: handle.adopted,
      ...(keyPushFailures.length > 0 ? { keyPushFailures } : {})
    })

    // Abort any prior stream BEFORE creating the new one: the per-install port
    // and token are stable across respawns, so a crashed engine's reconnecting
    // stream would otherwise silently re-attach to the new engine, duplicating
    // the event stream. Aborting first guarantees exactly one active stream.
    this.stream?.abort()
    this.stream = this.deps.createStream(client, {
      onEvent: (event) => this.deps.send(PUSH_CHANNELS.pipelineEvent, event),
      onHydrate: (jobs) => this.deps.send(PUSH_CHANNELS.jobsHydrated, jobs)
    })
    void this.stream.run()
  }

  /** Vault first, then push to the engine ("" clears, restoring its env fallback). */
  async putKey(provider: string, apiKey: string): Promise<void> {
    this.deps.vault.setKey(provider, apiKey)
    if (this.engineClient !== null) await this.engineClient.putKey(provider, apiKey)
  }

  /**
   * The quit sequence (design decision 3 / per P1, P7): abort our SSE
   * stream, POST /v1/shutdown, bounded wait (child exit event, or PID poll
   * for adopted engines), force-kill fallback. Runs before app exit and
   * before quitAndInstall.
   */
  async quit(opts: { timeoutMs?: number } = {}): Promise<void> {
    // Set FIRST, before reading this.handle: an in-flight respawn re-checks
    // this flag after every await, so a quit that begins mid-respawn wins the
    // race and no engine is spawned (or left running) afterward (C1/C2).
    this.quitting = true
    const handle = this.handle
    const client = this.engineClient
    if (handle === null || client === null) return
    this.handle = null
    this.engineClient = null
    await runQuitSequence(
      {
        abortEvents: () => this.stream?.abort(),
        postShutdown: () => client.shutdown(),
        waitExit: (timeoutMs) => this.waitEngineExit(handle, timeoutMs),
        forceKill: () => {
          if (handle.child !== null) {
            handle.child.kill('SIGKILL')
          } else {
            this.deps.killPid(handle.pid)
          }
        }
      },
      opts
    )
    this.setStatus({ state: 'stopped' })
  }

  /**
   * Manual recovery from the terminal `failed` state (IPC `engine:restart`).
   * Re-entrancy-guarded — a no-op while a (re)spawn is already in flight. It
   * does NOT go through `quit()`: the crash path already nulled the dead handle,
   * so there is no live engine to shut down. It clears `quitting`, resets the
   * respawn budget, aborts any lingering stream, then wires up a fresh engine
   * (a spawn failure falls into the same policy-driven failure path).
   */
  async restart(): Promise<void> {
    if (this.spawning) return
    this.spawning = true
    this.quitting = false
    this.deps.policy.reset()
    this.stream?.abort()
    this.stream = null
    this.handle = null
    this.engineClient = null
    this.setStatus({ state: 'starting' })
    let handle: EngineHandle
    try {
      handle = await this.deps.ensure()
    } catch (err) {
      this.spawning = false
      await this.handleSpawnFailure(err)
      return
    }
    if (this.quitting) {
      this.spawning = false
      this.killHandle(handle)
      this.setStatus({ state: 'stopped' })
      return
    }
    await this.wireUp(handle)
  }

  /**
   * Surface unexpected engine death. A spawned child exiting while we still own
   * it (and are not quitting) triggers a bounded auto-respawn. `quit()` sets
   * `quitting` and nulls `this.handle` before its shutdown sequence, so an exit
   * observed during (or after) quit is ignored. Adopted engines have no exit
   * event (per P7) and are not watched here.
   */
  private observeChildExit(handle: EngineHandle): void {
    if (handle.childExited === null) return
    void handle.childExited.then(() => {
      void this.handleUnexpectedExit(handle)
    })
  }

  /**
   * The crash → respawn path. Guards against a stale or graceful exit, nulls
   * ownership immediately (so `media` reports not-ready during the restart
   * window and `quit()` is never confused by a dead handle), then consults the
   * policy: give up to `failed`, or back off and respawn — re-checking
   * `quitting` after the backoff and again after `ensure()` resolves.
   */
  private async handleUnexpectedExit(handle: EngineHandle): Promise<void> {
    // Only respawn the engine we currently own, and never during quit.
    if (this.handle !== handle || this.quitting) return
    this.deps.log(`engine pid ${handle.pid} exited unexpectedly`)

    // Null ownership immediately (M5/C2): media → null, and a dead handle can
    // no longer confuse quit().
    this.handle = null
    this.engineClient = null
    this.stream?.abort()
    this.stream = null

    await this.scheduleRespawn(this.deps.policy.recordFailure(this.deps.now()))
  }

  /**
   * Translate a policy decision into UI + action: give up to `failed`, or bump
   * the attempt counter, broadcast `restarting`, and back off then respawn.
   */
  private async scheduleRespawn(decision: ReturnType<RespawnPolicy['recordFailure']>): Promise<void> {
    if (decision.action === 'give-up') {
      this.attemptNumber = 0
      this.setStatus({
        state: 'failed',
        message: 'engine keeps crashing — automatic restart gave up'
      })
      return
    }
    this.attemptNumber += 1
    this.setStatus({
      state: 'restarting',
      attempt: this.attemptNumber,
      maxAttempts: MAX_RESPAWN_ATTEMPTS
    })
    this.spawning = true
    await this.respawn(decision.delayMs)
  }

  /**
   * Back off, then respawn — with the C1/C2 quit checkpoints: re-check
   * `quitting` after the sleep AND after `ensure()` resolves (SIGKILL the
   * just-spawned child if quit won the race). A failed `ensure()` counts as a
   * failure and recurses into the policy.
   */
  private async respawn(delayMs: number): Promise<void> {
    await this.deps.sleep(delayMs)
    if (this.quitting) {
      this.spawning = false
      return
    }
    let handle: EngineHandle
    try {
      handle = await this.deps.ensure()
    } catch (err) {
      this.spawning = false
      await this.handleSpawnFailure(err)
      return
    }
    if (this.quitting) {
      // Quit won the race after the child was spawned: SIGKILL it instead of
      // wiring up a corpse, and settle on stopped.
      this.spawning = false
      this.killHandle(handle)
      this.setStatus({ state: 'stopped' })
      return
    }
    await this.wireUp(handle)
  }

  /** A spawn that could not even start counts as a failure → back into the policy. */
  private async handleSpawnFailure(err: unknown): Promise<void> {
    this.deps.log(`engine respawn failed to spawn: ${String(err)}`)
    if (this.quitting) {
      this.setStatus({ state: 'stopped' })
      return
    }
    await this.scheduleRespawn(this.deps.policy.recordFailure(this.deps.now()))
  }

  /** SIGKILL a spawned child, or force-kill a PID for an adopted handle. */
  private killHandle(handle: EngineHandle): void {
    if (handle.child !== null) handle.child.kill('SIGKILL')
    else this.deps.killPid(handle.pid)
  }

  private waitEngineExit(handle: EngineHandle, timeoutMs: number): Promise<boolean> {
    if (handle.childExited !== null) {
      return Promise.race([
        handle.childExited.then(() => true),
        this.deps.sleep(timeoutMs).then(() => false)
      ])
    }
    // Adopted engines are not our children and emit no exit event (per P7).
    return waitForPidExit(handle.pid, {
      timeoutMs,
      isAlive: this.deps.isAlive,
      sleep: this.deps.sleep
    })
  }

  private setStatus(status: EngineStatus): void {
    this.status = status
    this.deps.send(PUSH_CHANNELS.engineStatus, status)
  }
}
