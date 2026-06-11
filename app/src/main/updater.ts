import { PUSH_CHANNELS } from '../shared/ipc'
import type { UpdateStatus } from '../shared/ipc'

/**
 * Auto-update orchestration (design decisions 9, 10; app-packaging spec):
 * electron-updater against GitHub Releases with the FULL-DOWNLOAD strategy —
 * app and engine version in lockstep, so differential updates are deferred
 * until shell and engine release cadences decouple (the extraResources
 * layout keeps that path open).
 *
 * Updates download in the background; installation happens only after user
 * consent AND after the decision-3 quit sequence has terminated the engine —
 * an update never replaces files under a running engine.
 *
 * Everything electron-updater-shaped is injected (`AutoUpdaterLike`), so the
 * consent/quit/install ordering is unit-testable.
 */

/**
 * Flip to true in task 6.6 once signing credentials exist (Windows signtool
 * options + macOS notarization wired into electron-builder.config.cjs).
 * Until then every build is an unsigned dev-channel artifact: macOS
 * auto-update would be refused by Squirrel.Mac outright, and unsigned NSIS
 * updates are for manual dev verification only (see PODCAST_READER_FORCE_UPDATES).
 */
export const BUILD_SIGNED = false

export interface UpdateGateContext {
  isPackaged: boolean
  buildSigned: boolean
  env: Record<string, string | undefined>
}

export type UpdateGate = { enabled: true } | { enabled: false; reason: string }

/**
 * Updates are disabled in dev (unpackaged) and on unsigned builds.
 * `PODCAST_READER_FORCE_UPDATES=1` overrides the unsigned gate on a PACKAGED
 * build — the manual seam for verifying the unsigned NSIS update path
 * (task 6.3) before signing credentials exist.
 */
export function updaterGate(ctx: UpdateGateContext): UpdateGate {
  if (!ctx.isPackaged) {
    return { enabled: false, reason: 'updates disabled in development (unpackaged run)' }
  }
  if (!ctx.buildSigned && ctx.env['PODCAST_READER_FORCE_UPDATES'] !== '1') {
    return {
      enabled: false,
      reason:
        'updates disabled: unsigned dev-channel build (signing is a user-blocking ' +
        'prerequisite — tasks 6.4/6.5); set PODCAST_READER_FORCE_UPDATES=1 to test ' +
        'the unsigned NSIS update path manually'
    }
  }
  return { enabled: true }
}

/** The subset of electron-updater's `autoUpdater` this controller drives (test seam). */
export interface AutoUpdaterLike {
  autoDownload: boolean
  autoInstallOnAppQuit: boolean
  on(event: 'checking-for-update', listener: () => void): unknown
  on(event: 'update-available', listener: (info: { version: string }) => void): unknown
  on(event: 'update-not-available', listener: () => void): unknown
  on(event: 'update-downloaded', listener: (info: { version: string }) => void): unknown
  on(event: 'error', listener: (err: Error) => void): unknown
  checkForUpdates(): Promise<unknown>
  quitAndInstall(): void
}

export interface UpdaterDeps {
  autoUpdater: AutoUpdaterLike
  /** Consent prompt ("Restart and install vN now?"). */
  confirm(version: string): Promise<boolean>
  /** The decision-3 quit sequence — MUST complete before quitAndInstall. */
  quitEngine(): Promise<void>
  /** Broadcast to all renderer windows. */
  send(channel: string, payload: unknown): void
  log(message: string): void
  /** Periodic re-check interval; a declined update is re-offered on the next cycle. */
  recheckMs?: number
  scheduleRepeating?: (fn: () => void, ms: number) => void
}

const DEFAULT_RECHECK_MS = 4 * 60 * 60 * 1000

export class UpdaterController {
  status: UpdateStatus = { state: 'idle' }
  private downloadedVersion: string | null = null
  private installing = false

  constructor(private readonly deps: UpdaterDeps) {}

  /** Wire events, kick off the first check, and schedule periodic re-checks. */
  start(): void {
    const updater = this.deps.autoUpdater
    updater.autoDownload = true // background download (consent gates INSTALL, not download)
    updater.autoInstallOnAppQuit = false // install only via the explicit consent path
    updater.on('checking-for-update', () => this.setStatus({ state: 'checking' }))
    updater.on('update-available', (info) =>
      this.setStatus({ state: 'downloading', version: info.version })
    )
    updater.on('update-not-available', () => this.setStatus({ state: 'idle' }))
    updater.on('error', (err) => this.setStatus({ state: 'error', message: err.message }))
    updater.on('update-downloaded', (info) => {
      this.downloadedVersion = info.version
      this.setStatus({ state: 'ready', version: info.version })
      void this.offer(info.version)
    })
    this.check()
    const schedule =
      this.deps.scheduleRepeating ??
      ((fn: () => void, ms: number) => {
        setInterval(fn, ms)
      })
    schedule(() => this.check(), this.deps.recheckMs ?? DEFAULT_RECHECK_MS)
  }

  /** Renderer-initiated install (the "Restart to update" button on a deferred update). */
  async installNow(): Promise<void> {
    if (this.downloadedVersion === null) return
    await this.install(this.downloadedVersion)
  }

  private check(): void {
    // A declined (deferred) update is re-offered when the next check finds it
    // downloaded again; a hard failure is logged, never fatal.
    this.deps.autoUpdater.checkForUpdates().catch((err: unknown) => {
      this.deps.log(`update check failed: ${String(err)}`)
      this.setStatus({ state: 'error', message: String(err) })
    })
  }

  private async offer(version: string): Promise<void> {
    let accepted: boolean
    try {
      accepted = await this.deps.confirm(version)
    } catch (err) {
      this.deps.log(`update consent prompt failed: ${String(err)}`)
      return
    }
    if (!accepted) {
      this.setStatus({ state: 'deferred', version })
      return
    }
    await this.install(version)
  }

  private async install(version: string): Promise<void> {
    if (this.installing) return
    this.installing = true
    this.setStatus({ state: 'installing', version })
    try {
      // The quit sequence (abort SSE → POST /v1/shutdown → bounded wait →
      // force-kill) runs to completion BEFORE quitAndInstall: the installer
      // never replaces files under a running engine (app-packaging spec).
      await this.deps.quitEngine()
    } catch (err) {
      this.deps.log(`engine shutdown before update failed: ${String(err)}`)
    }
    this.deps.autoUpdater.quitAndInstall()
  }

  private setStatus(status: UpdateStatus): void {
    this.status = status
    this.deps.send(PUSH_CHANNELS.updateStatus, status)
  }
}
