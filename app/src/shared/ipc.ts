import type {
  EngineSettings,
  JobRecord,
  KeyTestResult,
  LibraryEntry,
  PipelineEvent,
  ProviderInfo,
  SettingsUpdate
} from './types'

/**
 * The typed IPC surface between renderer and main (design decision 4).
 *
 * The renderer is credential-free: every engine interaction crosses this
 * bridge; the bearer token and all HTTP/SSE traffic stay in the main process.
 */

/** Renderer → main request channels (`ipcRenderer.invoke` / `ipcMain.handle`). */
export const CHANNELS = {
  engineGetStatus: 'engine:get-status',
  jobsSubmit: 'jobs:submit',
  jobsList: 'jobs:list',
  jobsGet: 'jobs:get',
  jobsConfirm: 'jobs:confirm',
  jobsDismiss: 'jobs:dismiss',
  libraryList: 'library:list',
  libraryTranscript: 'library:transcript',
  settingsGet: 'settings:get',
  settingsPut: 'settings:put',
  keysPut: 'keys:put',
  keysTest: 'keys:test',
  keysStorageMode: 'keys:storage-mode',
  providersList: 'providers:list',
  updateGetStatus: 'update:get-status',
  updateInstall: 'update:install'
} as const

/** Main → renderer push channels (`webContents.send`). */
export const PUSH_CHANNELS = {
  /** EngineStatus changes (starting / ready / failed / stopped). */
  engineStatus: 'engine:status',
  /** Live PipelineEvents forwarded from the main-process SSE consumer. */
  pipelineEvent: 'engine:event',
  /** Full job list after every SSE (re)connect — records are the source of truth. */
  jobsHydrated: 'jobs:hydrated',
  /** A validated podcast-reader:// request landed as an awaiting-confirmation job. */
  protocolRequest: 'protocol:request',
  /** Auto-update lifecycle (UpdateStatus) from the main-process UpdaterController. */
  updateStatus: 'update:status'
} as const

export type EngineStatus =
  | { state: 'starting' }
  | { state: 'ready'; port: number; version: string; adopted: boolean; pid: number }
  | { state: 'failed'; message: string }
  | { state: 'stopped' }

/**
 * Auto-update lifecycle (design decision 9): background download, consent
 * prompt, engine-quit-then-install. `disabled` carries the gate reason
 * (dev run / unsigned build — see `updaterGate`).
 */
export type UpdateStatus =
  | { state: 'disabled'; reason: string }
  | { state: 'idle' }
  | { state: 'checking' }
  | { state: 'downloading'; version: string }
  | { state: 'ready'; version: string }
  | { state: 'deferred'; version: string }
  | { state: 'installing'; version: string }
  | { state: 'error'; message: string }

/** What the preload bridge exposes as `window.api`. */
export interface PodcastReaderApi {
  getEngineStatus(): Promise<EngineStatus>
  submitJob(req: {
    source: string
    title?: string | null
    requiresConfirmation?: boolean
  }): Promise<JobRecord>
  listJobs(): Promise<JobRecord[]>
  getJob(jobId: string): Promise<JobRecord>
  confirmJob(jobId: string): Promise<JobRecord>
  dismissJob(jobId: string): Promise<void>
  listLibrary(): Promise<LibraryEntry[]>
  transcriptHtml(sourceId: string): Promise<string>
  getSettings(): Promise<EngineSettings>
  putSettings(settings: SettingsUpdate): Promise<EngineSettings>
  /** Write-only: stores in the vault and pushes to the engine ("" clears). */
  putKey(provider: string, apiKey: string): Promise<void>
  testKey(provider: string, apiKey?: string): Promise<KeyTestResult>
  keyStorageMode(): Promise<'encrypted' | 'session-memory'>
  listProviders(): Promise<ProviderInfo[]>
  /** Resolve a dropped File's real filesystem path (webUtils.getPathForFile). */
  getPathForFile(file: File): string
  getUpdateStatus(): Promise<UpdateStatus>
  /** Apply a downloaded update now (quit sequence first, then quitAndInstall). */
  installUpdate(): Promise<void>
  onEngineStatus(listener: (status: EngineStatus) => void): () => void
  onPipelineEvent(listener: (event: PipelineEvent) => void): () => void
  onJobsHydrated(listener: (jobs: JobRecord[]) => void): () => void
  onProtocolRequest(listener: (job: JobRecord) => void): () => void
  onUpdateStatus(listener: (status: UpdateStatus) => void): () => void
}
