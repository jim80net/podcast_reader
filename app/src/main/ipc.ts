import { CHANNELS } from '../shared/ipc'
import type { EngineManager } from './engine-manager'
import type { SubmitJobRequest, UpdateStatus } from '../shared/ipc'
import type { SettingsUpdate } from '../shared/types'

/**
 * Main-process side of the typed IPC surface (design decision 4): each
 * renderer `invoke` maps to one `EngineClient` call. The bearer token never
 * crosses this boundary — responses carry engine payloads only.
 */

/** The subset of `ipcMain` used here (test seam). */
export interface IpcMainLike {
  // `any` matches electron's own listener signature, so both the real
  // ipcMain and unknown-typed test fakes are assignable.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  handle(channel: string, listener: (event: any, ...args: any[]) => unknown): void
}

/** The renderer-facing slice of the auto-updater (UpdaterController or the disabled gate). */
export interface UpdaterAccess {
  status(): UpdateStatus
  installNow(): Promise<void>
}

/** The renderer-facing slice of the app config (AppConfigStore). */
export interface AppConfigAccess {
  isFirstRunComplete(): boolean
  markFirstRunComplete(): void
}

export function registerIpcHandlers(
  ipcMain: IpcMainLike,
  manager: EngineManager,
  updates: UpdaterAccess,
  config: AppConfigAccess
): void {
  const client = () => {
    const c = manager.client
    if (c === null) throw new Error('engine is not ready')
    return c
  }

  ipcMain.handle(CHANNELS.engineGetStatus, () => manager.status)
  ipcMain.handle(CHANNELS.keysStorageMode, () => manager.keyStorageMode)

  ipcMain.handle(CHANNELS.jobsSubmit, (_e, req: SubmitJobRequest) =>
    client().submitJob({
      source: req.source,
      title: req.title ?? null,
      requires_confirmation: req.requiresConfirmation ?? false,
      overrides: req.overrides
    })
  )
  ipcMain.handle(CHANNELS.jobsList, () => client().listJobs())
  ipcMain.handle(CHANNELS.jobsGet, (_e, jobId: string) => client().getJob(jobId))
  ipcMain.handle(CHANNELS.jobsConfirm, (_e, jobId: string) => client().confirmJob(jobId))
  ipcMain.handle(CHANNELS.jobsDismiss, (_e, jobId: string) => client().discardJob(jobId))

  ipcMain.handle(CHANNELS.libraryList, () => client().listLibrary())
  ipcMain.handle(CHANNELS.libraryTranscript, (_e, sourceId: string) =>
    client().transcriptHtml(sourceId)
  )
  // Media metadata only — bytes load via the app:// scheme (media-protocol.ts),
  // never over IPC, so the token never reaches the renderer (app-shell spec).
  ipcMain.handle(CHANNELS.mediaInfo, (_e, sourceId: string) => client().mediaInfo(sourceId))
  // The loopback URL of the tokenless engine-hosted YouTube embed page. The
  // renderer loads it as an iframe src so the player gets a real http origin
  // (Error 152/153 fix); reuses the engine coordinates the app:// handler uses.
  ipcMain.handle(CHANNELS.youtubeEmbedUrl, (_e, videoId: string): string | null => {
    const engine = manager.media
    if (engine === null) return null
    return `${engine.baseUrl}/v1/embed/${encodeURIComponent(videoId)}`
  })

  ipcMain.handle(CHANNELS.settingsGet, () => client().getSettings())
  ipcMain.handle(CHANNELS.settingsPut, (_e, settings: SettingsUpdate) =>
    client().putSettings(settings)
  )

  // Key writes go through the manager: vault first, then push to the engine.
  ipcMain.handle(CHANNELS.keysPut, (_e, provider: string, apiKey: string) =>
    manager.putKey(provider, apiKey)
  )
  ipcMain.handle(CHANNELS.keysTest, (_e, provider: string, apiKey?: string) =>
    client().testKey(provider, apiKey)
  )
  ipcMain.handle(CHANNELS.providersList, () => client().listProviders())

  ipcMain.handle(CHANNELS.packsList, () => client().listPacks())
  ipcMain.handle(CHANNELS.packsInstall, (_e, packId: string) => client().installPack(packId))
  ipcMain.handle(CHANNELS.packsUninstall, (_e, packId: string) => client().uninstallPack(packId))

  // Extension pairing: mint engine-side, compose with the port so Settings
  // can show the combined <port>-<code> paste string (design decision 11).
  // The code crosses this bridge exactly once, render-bound — never logged.
  ipcMain.handle(CHANNELS.pairStart, async () => {
    const minted = await client().mintPairing()
    const port = manager.port
    if (port === null) throw new Error('engine is not ready')
    return { port, code: minted.code, expires_at: minted.expires_at }
  })

  // Cookie jars: metadata listing + delete only — jar content has no IPC path.
  ipcMain.handle(CHANNELS.cookiesList, () => client().listCookieJars())
  ipcMain.handle(CHANNELS.cookiesDelete, (_e, domain: string) => client().deleteCookieJar(domain))

  // First-run flag (setup wizard gate) — app-side state, no engine involved.
  ipcMain.handle(CHANNELS.firstRunGet, () => config.isFirstRunComplete())
  ipcMain.handle(CHANNELS.firstRunComplete, () => config.markFirstRunComplete())

  ipcMain.handle(CHANNELS.updateGetStatus, () => updates.status())
  ipcMain.handle(CHANNELS.updateInstall, () => updates.installNow())

  // Manual recovery from the terminal `failed` state — respawns a fresh engine
  // without going through the quit sequence (engine-respawn-supervision design).
  ipcMain.handle(CHANNELS.engineRestart, () => manager.restart())
}
