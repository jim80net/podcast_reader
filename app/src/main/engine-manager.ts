import { runQuitSequence, waitForPidExit } from './quit'
import { PUSH_CHANNELS } from '../shared/ipc'
import type { EngineHandle } from './engine'
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
  log(message: string): void
}

export class EngineManager {
  status: EngineStatus = { state: 'starting' }
  private handle: EngineHandle | null = null
  private engineClient: EngineClient | null = null
  private stream: { run(): Promise<void>; abort(): void } | null = null

  constructor(private readonly deps: ManagerDeps) {}

  /** The engine client, for IPC handlers. Null until the engine is ready. */
  get client(): EngineClient | null {
    return this.engineClient
  }

  get keyStorageMode(): KeyStorageMode {
    return this.deps.vault.mode
  }

  async start(): Promise<void> {
    this.setStatus({ state: 'starting' })
    let handle: EngineHandle
    try {
      handle = await this.deps.ensure()
    } catch (err) {
      this.setStatus({ state: 'failed', message: err instanceof Error ? err.message : String(err) })
      return
    }
    this.handle = handle
    this.observeChildExit(handle)
    const client = this.deps.createClient(handle)
    this.engineClient = client

    // Push-at-engine-start (design decision 5): every vaulted key reaches
    // engine memory BEFORE the renderer hears "ready" — no job submitted
    // after readiness can miss its key.
    for (const [provider, key] of Object.entries(this.deps.vault.keys())) {
      try {
        await client.putKey(provider, key)
      } catch (err) {
        this.deps.log(`key push for ${provider} failed: ${String(err)}`)
      }
    }

    this.setStatus({
      state: 'ready',
      port: handle.port,
      pid: handle.pid,
      version: handle.version,
      adopted: handle.adopted
    })

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
   * Surface unexpected engine death: a spawned child exiting while we still
   * own it becomes a visible `failed` status. `quit()` nulls `this.handle`
   * synchronously before it runs the shutdown sequence, so an exit observed
   * during (or after) quit is ignored. Adopted engines have no exit event
   * (per P7) and are not watched here. Respawn supervision is a follow-up —
   * for now the user restarts the app.
   */
  private observeChildExit(handle: EngineHandle): void {
    if (handle.childExited === null) return
    void handle.childExited.then(() => {
      if (this.handle !== handle) return
      this.deps.log(`engine pid ${handle.pid} exited unexpectedly`)
      this.setStatus({ state: 'failed', message: 'engine exited unexpectedly' })
    })
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
