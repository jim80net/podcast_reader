import { CHANNELS } from '../shared/ipc'
import type { EngineManager } from './engine-manager'
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

export function registerIpcHandlers(ipcMain: IpcMainLike, manager: EngineManager): void {
  const client = () => {
    const c = manager.client
    if (c === null) throw new Error('engine is not ready')
    return c
  }

  ipcMain.handle(CHANNELS.engineGetStatus, () => manager.status)
  ipcMain.handle(CHANNELS.keysStorageMode, () => manager.keyStorageMode)

  ipcMain.handle(
    CHANNELS.jobsSubmit,
    (_e, req: { source: string; title?: string | null; requiresConfirmation?: boolean }) =>
      client().submitJob({
        source: req.source,
        title: req.title ?? null,
        requires_confirmation: req.requiresConfirmation ?? false
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
}
