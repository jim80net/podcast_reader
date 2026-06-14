import type {
  CookieJarInfo,
  EngineSettings,
  JobRecord,
  KeyTestResult,
  LibraryEntry,
  MediaInfo,
  PacksResponse,
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
  mediaInfo: 'media:info',
  settingsGet: 'settings:get',
  settingsPut: 'settings:put',
  keysPut: 'keys:put',
  keysTest: 'keys:test',
  keysStorageMode: 'keys:storage-mode',
  providersList: 'providers:list',
  packsList: 'packs:list',
  packsInstall: 'packs:install',
  packsUninstall: 'packs:uninstall',
  firstRunGet: 'first-run:get',
  firstRunComplete: 'first-run:complete',
  pairStart: 'pair:start',
  cookiesList: 'cookies:list',
  cookiesDelete: 'cookies:delete',
  updateGetStatus: 'update:get-status',
  updateInstall: 'update:install',
  engineRestart: 'engine:restart'
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
  | {
      state: 'ready'
      port: number
      version: string
      adopted: boolean
      pid: number
      /** Providers whose vaulted key failed to push at startup (absent when all succeeded). */
      keyPushFailures?: string[]
    }
  /**
   * A spawned engine exited unexpectedly and the bounded respawn policy is
   * retrying. `attempt`/`maxAttempts` drive the renderer's "Reconnecting…
   * (N/M)" banner (engine-respawn-supervision design).
   */
  | { state: 'restarting'; attempt: number; maxAttempts: number }
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

/** The `jobs:submit` request — one shared shape for the renderer call and the main handler. */
export interface SubmitJobRequest {
  source: string
  title?: string | null
  requiresConfirmation?: boolean
}

/**
 * The `pair:start` response: the engine's `PairStartResponse` composed with
 * the engine port the main process is connected to, so Settings can render
 * the combined `<port>-<code>` paste string (design decision 11).
 */
export interface PairingDisplay {
  port: number
  code: string
  expires_at: number
}

/** What the preload bridge exposes as `window.api`. */
export interface PodcastReaderApi {
  getEngineStatus(): Promise<EngineStatus>
  submitJob(req: SubmitJobRequest): Promise<JobRecord>
  listJobs(): Promise<JobRecord[]>
  getJob(jobId: string): Promise<JobRecord>
  confirmJob(jobId: string): Promise<JobRecord>
  dismissJob(jobId: string): Promise<void>
  listLibrary(): Promise<LibraryEntry[]>
  transcriptHtml(sourceId: string): Promise<string>
  /**
   * A library entry's playback classification + prep status (`GET
   * /v1/media/{id}/info`). Only this metadata crosses IPC; media bytes reach
   * the renderer solely through the main-mediated `app://media/<id>` scheme.
   */
  mediaInfo(sourceId: string): Promise<MediaInfo>
  getSettings(): Promise<EngineSettings>
  putSettings(settings: SettingsUpdate): Promise<EngineSettings>
  /** Write-only: stores in the vault and pushes to the engine ("" clears). */
  putKey(provider: string, apiKey: string): Promise<void>
  testKey(provider: string, apiKey?: string): Promise<KeyTestResult>
  keyStorageMode(): Promise<'encrypted' | 'session-memory'>
  listProviders(): Promise<ProviderInfo[]>
  /** Hardware block + per-pack status — the hydration source of truth for pack state. */
  listPacks(): Promise<PacksResponse>
  /** Start (or idempotently re-request) an async pack install; progress arrives as pack events. */
  installPack(packId: string): Promise<void>
  uninstallPack(packId: string): Promise<void>
  /** App-side first-run flag (setup wizard): true once setup was completed or skipped. */
  isFirstRunComplete(): Promise<boolean>
  markFirstRunComplete(): Promise<void>
  /** Mint an extension pairing code (engine `POST /v1/pair`) plus the engine port. */
  startPairing(): Promise<PairingDisplay>
  /** Captured cookie-jar metadata (`GET /v1/cookies`) — domains and dates, never values. */
  listCookieJars(): Promise<CookieJarInfo[]>
  deleteCookieJar(domain: string): Promise<void>
  /** Resolve a dropped File's real filesystem path (webUtils.getPathForFile). */
  getPathForFile(file: File): string
  getUpdateStatus(): Promise<UpdateStatus>
  /** Apply a downloaded update now (quit sequence first, then quitAndInstall). */
  installUpdate(): Promise<void>
  /** Manually respawn the engine after it reached the terminal `failed` state. */
  engineRestart(): Promise<void>
  onEngineStatus(listener: (status: EngineStatus) => void): () => void
  onPipelineEvent(listener: (event: PipelineEvent) => void): () => void
  onJobsHydrated(listener: (jobs: JobRecord[]) => void): () => void
  onProtocolRequest(listener: (job: JobRecord) => void): () => void
  onUpdateStatus(listener: (status: UpdateStatus) => void): () => void
}
