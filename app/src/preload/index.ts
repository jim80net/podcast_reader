import { contextBridge, ipcRenderer, webUtils } from 'electron'

import { CHANNELS, PUSH_CHANNELS } from '../shared/ipc'
import type { EngineStatus, PodcastReaderApi, UpdateStatus } from '../shared/ipc'
import type { JobRecord, PipelineEvent, SettingsUpdate } from '../shared/types'

/**
 * The contextBridge API (design decisions 4, 7): the renderer's ONLY door.
 * Runs with contextIsolation + sandbox on, nodeIntegration off; nothing
 * credential-shaped crosses here — engine HTTP/SSE happens main-side and the
 * renderer sees payloads and forwarded events only.
 */

function subscribe<T>(channel: string, listener: (payload: T) => void): () => void {
  const wrapped = (_event: Electron.IpcRendererEvent, payload: T): void => listener(payload)
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.removeListener(channel, wrapped)
}

const api: PodcastReaderApi = {
  getEngineStatus: () => ipcRenderer.invoke(CHANNELS.engineGetStatus),
  submitJob: (req) => ipcRenderer.invoke(CHANNELS.jobsSubmit, req),
  listJobs: () => ipcRenderer.invoke(CHANNELS.jobsList),
  getJob: (jobId) => ipcRenderer.invoke(CHANNELS.jobsGet, jobId),
  confirmJob: (jobId) => ipcRenderer.invoke(CHANNELS.jobsConfirm, jobId),
  dismissJob: (jobId) => ipcRenderer.invoke(CHANNELS.jobsDismiss, jobId),
  listLibrary: () => ipcRenderer.invoke(CHANNELS.libraryList),
  transcriptHtml: (sourceId) => ipcRenderer.invoke(CHANNELS.libraryTranscript, sourceId),
  mediaInfo: (sourceId) => ipcRenderer.invoke(CHANNELS.mediaInfo, sourceId),
  getSettings: () => ipcRenderer.invoke(CHANNELS.settingsGet),
  putSettings: (settings: SettingsUpdate) => ipcRenderer.invoke(CHANNELS.settingsPut, settings),
  putKey: (provider, apiKey) => ipcRenderer.invoke(CHANNELS.keysPut, provider, apiKey),
  testKey: (provider, apiKey) => ipcRenderer.invoke(CHANNELS.keysTest, provider, apiKey),
  keyStorageMode: () => ipcRenderer.invoke(CHANNELS.keysStorageMode),
  listProviders: () => ipcRenderer.invoke(CHANNELS.providersList),
  listPacks: () => ipcRenderer.invoke(CHANNELS.packsList),
  installPack: (packId) => ipcRenderer.invoke(CHANNELS.packsInstall, packId),
  uninstallPack: (packId) => ipcRenderer.invoke(CHANNELS.packsUninstall, packId),
  isFirstRunComplete: () => ipcRenderer.invoke(CHANNELS.firstRunGet),
  markFirstRunComplete: () => ipcRenderer.invoke(CHANNELS.firstRunComplete),
  startPairing: () => ipcRenderer.invoke(CHANNELS.pairStart),
  listCookieJars: () => ipcRenderer.invoke(CHANNELS.cookiesList),
  deleteCookieJar: (domain) => ipcRenderer.invoke(CHANNELS.cookiesDelete, domain),
  getPathForFile: (file) => webUtils.getPathForFile(file),
  getUpdateStatus: () => ipcRenderer.invoke(CHANNELS.updateGetStatus),
  installUpdate: () => ipcRenderer.invoke(CHANNELS.updateInstall),
  engineRestart: () => ipcRenderer.invoke(CHANNELS.engineRestart),
  onEngineStatus: (listener: (status: EngineStatus) => void) =>
    subscribe(PUSH_CHANNELS.engineStatus, listener),
  onPipelineEvent: (listener: (event: PipelineEvent) => void) =>
    subscribe(PUSH_CHANNELS.pipelineEvent, listener),
  onJobsHydrated: (listener: (jobs: JobRecord[]) => void) =>
    subscribe(PUSH_CHANNELS.jobsHydrated, listener),
  onProtocolRequest: (listener: (job: JobRecord) => void) =>
    subscribe(PUSH_CHANNELS.protocolRequest, listener),
  onUpdateStatus: (listener: (status: UpdateStatus) => void) =>
    subscribe(PUSH_CHANNELS.updateStatus, listener)
}

contextBridge.exposeInMainWorld('api', api)
