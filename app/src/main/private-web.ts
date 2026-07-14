import type { AppConfigStore } from './app-config'
import type { ServeManager, ServeTransportState } from './serve-manager'
import type { PrivateWebStatus } from '../shared/ipc'

export interface PrivateWebDeps {
  config: Pick<AppConfigStore, 'privateWebEnabled' | 'setPrivateWebEnabled'>
  serve: Pick<
    ServeManager,
    'needsReconciliation' | 'setTerminalHandler' | 'reconcile' | 'start' | 'stop'
  >
  enginePort(): number | null
  send(status: PrivateWebStatus): void
  log(message: string): void
}

/** Coordinates the opt-in preference with engine and guardian lifetimes. */
export class PrivateWebController {
  private statusValue: PrivateWebStatus
  private launchAllowed = false
  private operation: Promise<void> = Promise.resolve()

  constructor(private readonly deps: PrivateWebDeps) {
    this.statusValue = deps.config.privateWebEnabled()
      ? { state: 'starting' }
      : { state: 'disabled' }
    deps.serve.setTerminalHandler((state) => {
      void this.serialized(async () => {
        this.setStatus(
          state.state === 'idle' && !this.deps.config.privateWebEnabled()
            ? { state: 'disabled' }
            : this.toPublic(state)
        )
      })
    })
  }

  get status(): PrivateWebStatus {
    return this.statusValue
  }

  async beforeEngineSpawn(): Promise<void> {
    await this.serialized(() => this.beforeEngineSpawnNow())
  }

  async afterEngineReady(port: number): Promise<void> {
    await this.serialized(() => this.afterEngineReadyNow(port))
  }

  async beforeEngineStop(): Promise<void> {
    await this.serialized(() => this.beforeEngineStopNow())
  }

  async setEnabled(enabled: boolean): Promise<PrivateWebStatus> {
    return this.serialized(async () => {
      if (!enabled) {
        const cleanup = await this.beforeEngineStopNow()
        this.deps.config.setPrivateWebEnabled(false)
        this.launchAllowed = false
        if (cleanup.state === 'idle') {
          this.setStatus({ state: 'disabled' })
        }
        return this.statusValue
      }

      this.deps.config.setPrivateWebEnabled(true)
      await this.beforeEngineSpawnNow()
      const port = this.deps.enginePort()
      if (this.launchAllowed && port !== null) await this.afterEngineReadyNow(port)
      return this.statusValue
    })
  }

  private async beforeEngineSpawnNow(): Promise<void> {
    this.launchAllowed = false
    const enabled = this.deps.config.privateWebEnabled()
    if (!enabled && !this.deps.serve.needsReconciliation()) {
      this.setStatus({ state: 'disabled' })
      return
    }
    this.setStatus({ state: 'starting' })
    try {
      const reconciled = await this.deps.serve.reconcile()
      this.launchAllowed = enabled && reconciled.state === 'idle'
      if (reconciled.state !== 'idle') this.setStatus(this.toPublic(reconciled))
      else if (!enabled) this.setStatus({ state: 'disabled' })
    } catch (cause) {
      this.setStatus({ state: 'error', message: `Tailscale status unavailable: ${String(cause)}` })
    }
  }

  private async afterEngineReadyNow(port: number): Promise<void> {
    if (!this.deps.config.privateWebEnabled() || !this.launchAllowed) return
    try {
      this.setStatus(this.toPublic(await this.deps.serve.start(port)))
    } catch (cause) {
      this.setStatus({ state: 'error', message: `Private web access could not start: ${String(cause)}` })
    }
  }

  private async beforeEngineStopNow(): Promise<ServeTransportState> {
    try {
      const stopped = await this.deps.serve.stop()
      if (stopped.state !== 'idle') this.setStatus(this.toPublic(stopped))
      return stopped
    } catch (cause) {
      const failed = {
        state: 'error' as const,
        message: `Private web cleanup failed: ${String(cause)}`
      }
      this.setStatus(failed)
      return failed
    }
  }

  private toPublic(state: ServeTransportState): PrivateWebStatus {
    if (state.state === 'idle') return { state: 'starting' }
    return state
  }

  private setStatus(status: PrivateWebStatus): void {
    this.statusValue = status
    this.deps.log(`private web: ${status.state}`)
    this.deps.send(status)
  }

  private serialized<T>(action: () => Promise<T>): Promise<T> {
    const result = this.operation.then(action, action)
    this.operation = result.then(
      () => undefined,
      () => undefined
    )
    return result
  }
}
