/**
 * TypeScript mirrors of the engine's typed boundaries.
 *
 * Each shape is comment-pinned to its Python source of truth; the real-engine
 * smoke test (task 7.3) asserts exact key-set equality against live payloads.
 * Keep field order and names identical to the Python definitions.
 */

// -- src/podcast_reader/types.py:11 (StepName) --
export type StepName =
  | 'resolve'
  | 'captions'
  | 'download'
  | 'transcribe'
  | 'diarize'
  | 'chapters'
  | 'render'

// -- src/podcast_reader/types.py:16 (EventKind) --
// pack_state / pack_progress ride the same SSE stream as job events; they
// carry data.pack_id and NEVER a job_id (per Q5 — job_id presence is the
// renderer's job/pack discriminator). media_state / media_progress carry
// data.source_id and likewise NEVER a job_id (media-playback).
export type EventKind =
  | 'step_started'
  | 'step_progress'
  | 'step_finished'
  | 'warning'
  | 'job_done'
  | 'job_failed'
  | 'pack_state'
  | 'pack_progress'
  | 'media_state'
  | 'media_progress'

// -- src/podcast_reader/types.py:26 (JobState) --
export type JobState =
  | 'queued'
  | 'awaiting-confirmation'
  | 'running'
  | 'done'
  | 'failed'
  | 'interrupted'

// -- src/podcast_reader/types.py:28 (JOB_STATES) --
export const JOB_STATES: readonly JobState[] = [
  'queued',
  'awaiting-confirmation',
  'running',
  'done',
  'failed',
  'interrupted'
] as const

// -- src/podcast_reader/types.py:38 (PipelineEvent) --
export interface PipelineEvent {
  kind: EventKind
  step: StepName | null
  message: string
  data: Record<string, unknown>
}

// -- src/podcast_reader/types.py:62 (JobError) --
export interface JobError {
  code: string
  message: string
  hint: string
}

// -- src/podcast_reader/types.py:84 (PipelineResult) --
export interface PipelineResult {
  json_path: string
  chapters_path: string | null
  html_path: string
  title: string
}

// -- src/podcast_reader/types.py:91 (JobRecord) --
export interface JobRecord {
  id: string
  source: string
  title: string | null
  state: JobState
  error: JobError | null
  events: PipelineEvent[]
  result: PipelineResult | null
  created_at: number
  updated_at: number
}

// -- src/podcast_reader/types.py:103 (LibraryEntry) --
export interface LibraryEntry {
  source_id: string
  source: string
  title: string
  html_path: string
  created_at: number
}

// -- src/podcast_reader/types.py:111 (EngineSettings) --
export interface EngineSettings {
  whisper_model: string
  whisper_lang: string
  whisper_device: string
  sentences: number
  library_dir: string
  chapter_model: string // "" means: the chapter provider's default model
  chapter_provider: string // a podcast_reader.providers.PROVIDERS key
  custom_provider_url: string // base URL for the "custom" provider ("" otherwise)
  diarize: boolean // default false; engine warns-and-skips when the pack is absent
  media_cache_max_bytes: number // LRU cap for the lazy media cache (media-playback)
}

// -- src/podcast_reader/engine/app.py:100 (SettingsBody) --
// PUT /v1/settings body: post-Phase-1 fields are optional ("keep current").
export interface SettingsUpdate {
  whisper_model: string
  whisper_lang: string
  whisper_device: string
  sentences: number
  library_dir: string
  chapter_model: string
  chapter_provider?: string
  custom_provider_url?: string
  diarize?: boolean
  media_cache_max_bytes?: number
}

// -- src/podcast_reader/types.py (MediaInfo) --
// media-playback: a library entry's playback classification + prep status.
export type MediaKind = 'youtube' | 'video' | 'audio' | 'unavailable'
export type MediaStatus = 'ready' | 'preparing' | 'unavailable'

export interface MediaInfo {
  kind: MediaKind
  youtube_id: string // "" unless kind === 'youtube'
  duration_s: number // 0 when unknown
  status: MediaStatus
  progress: number // 0..1 while preparing; 1 when ready
}

// -- src/podcast_reader/engine/app.py:50 (JobSubmission) --
export interface JobSubmission {
  source: string
  title?: string | null
  requires_confirmation?: boolean
}

// -- src/podcast_reader/engine/app.py:77 (KeyTestResult) --
export interface KeyTestResult {
  ok: boolean
  detail: string | null
}

// -- src/podcast_reader/engine/app.py:88 (ProviderInfo) --
export interface ProviderInfo {
  id: string
  default_model: string
  key_available: boolean
}

// -- src/podcast_reader/engine/packs.py:41 (PackKind) --
export type PackKind = 'runtime' | 'model' | 'worker'

// -- src/podcast_reader/engine/packs.py:42 (PackState) --
export type PackState =
  | 'not-installed'
  | 'resumable'
  | 'installing'
  | 'installed'
  | 'incompatible'
  | 'failed'
  | 'unavailable'

// -- src/podcast_reader/engine/packs.py:112 (PackProgress) --
export interface PackProgress {
  bytes: number
  total: number
}

// -- src/podcast_reader/engine/packs.py:119 (HardwareInfo) --
export interface HardwareInfo {
  platform: string // a Python sys.platform value ("win32", "darwin", "linux")
  nvidia_gpu: boolean
  gpu_names: string[]
}

// -- src/podcast_reader/engine/packs.py:141 (PackInstallError) --
export interface PackInstallError {
  code: string
  message: string
}

// -- src/podcast_reader/engine/packs.py:62 (LicenseNotice) --
export interface LicenseNotice {
  name: string
  text: string
}

// -- src/podcast_reader/engine/packs.py:127 (PackStatus) --
export interface PackStatus {
  id: string
  kind: PackKind
  display_name: string
  size: number // total download size in bytes (0 for unpublished entries)
  state: PackState
  recommended: boolean
  installed_version: string | null
  progress: PackProgress | null
  error: PackInstallError | null
  // Attribution notices Settings renders (manifest-recorded when installed,
  // registry otherwise — engine-authoritative either way, task 8.1).
  licenses: LicenseNotice[]
}

// -- src/podcast_reader/engine/packs.py:148 (PacksResponse) --
export interface PacksResponse {
  hardware: HardwareInfo
  packs: PackStatus[]
}

// -- src/podcast_reader/engine/app.py:137 (HealthInfo) --
export interface HealthInfo {
  version: string
  token_fingerprint: string
}

// -- src/podcast_reader/engine/app.py:179 (PairMintResponse) --
// POST /v1/pair response: the code lives only in engine process memory and
// in this one payload — never persisted, never logged. expires_at is epoch
// seconds (300 s TTL; a re-mint replaces the pending code).
export interface PairStartResponse {
  code: string
  expires_at: number
}

// -- src/podcast_reader/engine/cookies.py:48 (CookieJarInfo) --
// One GET /v1/cookies entry: metadata only — never cookie values.
export interface CookieJarInfo {
  domain: string
  created_at: number
}

// -- src/podcast_reader/engine/process.py:73 (DiscoveryInfo) --
export interface DiscoveryInfo {
  port: number
  pid: number
  token_fingerprint: string
  version: string
}

// -- src/podcast_reader/engine/settings.py:46 (EngineState) --
export interface EngineState {
  port: number
  token: string
}

// -- src/podcast_reader/engine/process.py:70 (READY_SENTINEL) --
export const READY_SENTINEL = 'PODCAST_READER_READY'

// -- src/podcast_reader/engine/process.py:69 (DISCOVERY_FILE) --
export const DISCOVERY_FILE = 'engine.json'

// -- src/podcast_reader/engine/settings.py:35 (ENGINE_STATE_FILE) --
export const ENGINE_STATE_FILE = 'engine-state.json'
