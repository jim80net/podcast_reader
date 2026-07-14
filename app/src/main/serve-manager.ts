import { SERVE_GENERATION } from './serve-journal'
import { classifyServeStatus } from './serve-status'
import type { ServeJournalRead, ServeOwnershipRecord } from './serve-journal'

export type GuardianEvent =
  | { event: 'bound'; target: string }
  | { event: 'ready'; target: string; url: string }
  | { event: 'stopped' }
  | { event: 'unowned'; severity: 'error' | 'conflict'; message: string }
  | { event: 'conflict'; message: string }
  | { event: 'error'; message: string }

export interface GuardianProcess {
  nextEvent(timeoutMs?: number | null): Promise<GuardianEvent>
  sendGo(): void
  closeLease(): void
  kill(): void
}

export interface ServeJournalLike {
  read(): ServeJournalRead
  write(record: ServeOwnershipRecord): void
  remove(): void
}

export interface ServeManagerDeps {
  journal: ServeJournalLike
  readStatus(): Promise<string>
  disableListener(): Promise<void>
  spawnGuardian(enginePort: number): GuardianProcess
  stopTimeoutMs?: number
}

export type ServeTransportState =
  | { state: 'idle' }
  | { state: 'ready'; url: string }
  | { state: 'conflict'; message: string }
  | { state: 'error'; message: string }

function conflict(message: string): ServeTransportState {
  return { state: 'conflict', message }
}

function error(message: string): ServeTransportState {
  return { state: 'error', message }
}

function validTarget(value: string): boolean {
  const match = /^http:\/\/127\.0\.0\.1:(\d{1,5})$/.exec(value)
  if (match === null) return false
  const port = Number(match[1])
  return port >= 1 && port <= 65535
}

function validPrivateUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return (
      url.protocol === 'https:' &&
      url.username === '' &&
      url.password === '' &&
      validTailnetHostname(url.hostname) &&
      url.port === '' &&
      url.pathname === '/web/' &&
      url.search === '' &&
      url.hash === ''
    )
  } catch {
    return false
  }
}

function validTailnetHostname(host: string): boolean {
  if (host.length > 253 || !host.endsWith('.ts.net')) return false
  return host.split('.').every(
    (label) =>
      label.length >= 1 &&
      label.length <= 63 &&
      /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
  )
}

/** Owns crash reconciliation and the Electron side of the guardian lease. */
export class ServeManager {
  private guardian: GuardianProcess | null = null
  private target: string | null = null
  private terminal: Promise<GuardianEvent> | null = null
  private stopping = false
  private terminalHandler: ((state: ServeTransportState) => void) | null = null
  private generation = 0
  private expectedCleanup = false

  constructor(private readonly deps: ServeManagerDeps) {}

  needsReconciliation(): boolean {
    return this.deps.journal.read().kind !== 'absent'
  }

  setTerminalHandler(handler: (state: ServeTransportState) => void): void {
    this.terminalHandler = handler
  }

  /** Reconcile an old pending/active record before any new engine is spawned. */
  async reconcile(): Promise<ServeTransportState> {
    const ownership = this.deps.journal.read()
    if (ownership.kind === 'conflict') return conflict(ownership.reason)

    const observed = classifyServeStatus(await this.deps.readStatus())
    if (observed.kind === 'conflict') return conflict(observed.reason)
    if (ownership.kind === 'absent') {
      return observed.kind === 'empty'
        ? { state: 'idle' }
        : conflict('HTTPS 443 is already configured outside Podcast Reader')
    }

    if (observed.kind === 'empty') {
      this.deps.journal.remove()
      return { state: 'idle' }
    }
    if (observed.target !== ownership.record.target) {
      return conflict('The Serve mapping no longer matches Podcast Reader ownership')
    }

    // Exact persisted listener + generation + target is the sole authority to
    // mutate. Unknown status shapes returned above before reaching this call.
    await this.deps.disableListener()
    const after = classifyServeStatus(await this.deps.readStatus())
    if (after.kind !== 'empty') {
      return conflict(
        after.kind === 'conflict'
          ? after.reason
          : 'The prior private mapping could not be removed safely'
      )
    }
    this.deps.journal.remove()
    return { state: 'idle' }
  }

  async start(enginePort: number): Promise<ServeTransportState> {
    if (this.guardian !== null) return error('Private web guardian is already running')
    this.expectedCleanup = false
    const guardian = this.deps.spawnGuardian(enginePort)
    const generation = ++this.generation
    this.guardian = guardian
    let bound: GuardianEvent
    try {
      bound = await guardian.nextEvent()
    } catch (cause) {
      guardian.kill()
      this.guardian = null
      return error(`Private web guardian failed before binding: ${String(cause)}`)
    }
    if (bound.event !== 'bound' || !validTarget(bound.target)) {
      guardian.kill()
      this.guardian = null
      return bound.event === 'conflict'
        ? conflict(bound.message)
        : error(bound.event === 'error' ? bound.message : 'Guardian returned an invalid gate')
    }

    const record: ServeOwnershipRecord = {
      state: 'pending',
      generation: SERVE_GENERATION,
      listener: 'https:443',
      target: bound.target
    }
    try {
      this.deps.journal.write(record)
      guardian.sendGo()
    } catch (cause) {
      guardian.closeLease()
      guardian.kill()
      this.guardian = null
      return error(`Private web ownership could not be persisted: ${String(cause)}`)
    }

    let ready: GuardianEvent
    try {
      ready = await guardian.nextEvent()
    } catch (cause) {
      guardian.closeLease()
      this.watchCleanup(guardian, bound.target, generation)
      return error(`Private web guardian failed during startup: ${String(cause)}`)
    }
    if (
      ready.event !== 'ready' ||
      ready.target !== bound.target ||
      !validPrivateUrl(ready.url)
    ) {
      guardian.closeLease()
      if (ready.event === 'unowned') {
        this.guardian = null
        this.deps.journal.remove()
        return ready.severity === 'conflict' ? conflict(ready.message) : error(ready.message)
      }
      this.watchCleanup(guardian, bound.target, generation)
      if (ready.event === 'conflict') return conflict(ready.message)
      return error(ready.event === 'error' ? ready.message : 'Guardian returned invalid readiness')
    }

    try {
      this.deps.journal.write({ ...record, state: 'active' })
    } catch (cause) {
      // The durable pending record still proves ownership. Revoke the lease;
      // the next startup reconciles pending against status before any spawn.
      guardian.closeLease()
      let settled: GuardianEvent
      try {
        settled = await guardian.nextEvent()
      } catch {
        // Keep the gate holder alive until status proves absence/change.
        this.watchCleanup(guardian, bound.target, generation)
        return error(`Private web ownership could not be promoted: ${String(cause)}`)
      }
      this.guardian = null
      this.target = null
      await this.finishTerminal(settled, bound.target, generation)
      return error(`Private web ownership could not be promoted: ${String(cause)}`)
    }
    this.watchCleanup(guardian, bound.target, generation)
    return { state: 'ready', url: ready.url }
  }

  async stop(): Promise<ServeTransportState> {
    const guardian = this.guardian
    const target = this.target
    if (guardian === null || target === null) {
      // Invalidate an unexpected-terminal settlement that may be awaiting a
      // status read after it detached the old guardian.
      this.generation += 1
      return this.needsReconciliation() ? this.reconcile() : { state: 'idle' }
    }
    this.stopping = true
    guardian.closeLease()
    let terminal: GuardianEvent
    try {
      const observed = this.terminal ?? guardian.nextEvent()
      let timeout: NodeJS.Timeout | null = null
      try {
        terminal = await Promise.race([
          observed,
          new Promise<GuardianEvent>((resolve) => {
            timeout = setTimeout(
              () => resolve({ event: 'error', message: 'Private web guardian stop timed out' }),
              this.deps.stopTimeoutMs ?? 15_000
            )
          })
        ])
      } finally {
        if (timeout !== null) clearTimeout(timeout)
      }
      if (terminal.event === 'error' && terminal.message === 'Private web guardian stop timed out') {
        // The guardian intentionally retains its gate while Tailscale status is
        // unprovable. Leave its lease watcher alive so the port cannot be reused
        // beneath a stale mapping; its eventual terminal event remains watched.
        this.stopping = false
        this.expectedCleanup = true
        return error(terminal.message)
      }
    } catch (cause) {
      this.guardian = null
      this.target = null
      this.terminal = null
      this.stopping = false
      return error(`Private web guardian exit was not verified: ${String(cause)}`)
    }
    this.guardian = null
    this.target = null
    this.terminal = null
    this.stopping = false
    this.expectedCleanup = false
    this.generation += 1
    return this.finishTerminal(terminal, target)
  }

  private async finishTerminal(
    terminal: GuardianEvent,
    target: string,
    expectedGeneration?: number
  ): Promise<ServeTransportState> {
    if (terminal.event === 'unowned') {
      if (expectedGeneration !== undefined && this.generation !== expectedGeneration) {
        return error('A newer private web session replaced this cleanup')
      }
      this.deps.journal.remove()
      return terminal.severity === 'conflict'
        ? conflict(terminal.message)
        : error(terminal.message)
    }
    if (terminal.event === 'conflict') return conflict(terminal.message)

    const observed = classifyServeStatus(await this.deps.readStatus())
    if (observed.kind === 'empty') {
      if (expectedGeneration !== undefined && this.generation !== expectedGeneration) {
        return error('A newer private web session replaced this cleanup')
      }
      this.deps.journal.remove()
      return terminal.event === 'stopped'
        ? { state: 'idle' }
        : error(terminal.event === 'error' ? terminal.message : 'Guardian stopped unexpectedly')
    }
    if (observed.kind === 'conflict') return conflict(observed.reason)
    return observed.target === target
      ? error('Private mapping removal could not be verified')
      : conflict('Serve mapping changed during cleanup; it was preserved')
  }

  private async handleUnexpectedTerminal(
    guardian: GuardianProcess,
    target: string,
    generation: number,
    event: GuardianEvent
  ): Promise<void> {
    if (this.guardian !== guardian || this.generation !== generation || this.stopping) return
    this.guardian = null
    this.target = null
    this.terminal = null
    try {
      const settled = await this.finishTerminal(event, target, generation)
      if (this.generation !== generation) return
      const expectedCleanup = this.expectedCleanup
      this.expectedCleanup = false
      this.terminalHandler?.(
        settled.state === 'idle' && !expectedCleanup
          ? error('Private web access stopped unexpectedly')
          : settled
      )
    } catch (cause) {
      if (this.generation !== generation) return
      this.terminalHandler?.(
        error(`Private web guardian cleanup could not be verified: ${String(cause)}`)
      )
    }
  }

  private watchCleanup(guardian: GuardianProcess, target: string, generation: number): void {
    this.guardian = guardian
    this.target = target
    // Startup transitions are bounded; active/cleanup supervision is not.
    const terminal = guardian.nextEvent(null)
    this.terminal = terminal
    void terminal.then(
      (event) => this.handleUnexpectedTerminal(guardian, target, generation, event),
      (cause: unknown) => this.handleUnexpectedFailure(guardian, generation, cause)
    )
  }

  private handleUnexpectedFailure(
    guardian: GuardianProcess,
    generation: number,
    cause: unknown
  ): void {
    if (this.guardian !== guardian || this.generation !== generation || this.stopping) return
    this.guardian = null
    this.target = null
    this.terminal = null
    this.expectedCleanup = false
    this.terminalHandler?.(error(`Private web guardian exited unexpectedly: ${String(cause)}`))
  }
}
