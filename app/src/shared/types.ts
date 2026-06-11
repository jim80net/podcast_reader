/**
 * TypeScript mirrors of the engine's typed boundaries.
 *
 * Each shape is comment-pinned to its Python source of truth; the real-engine
 * smoke test (task 7.3) asserts exact key-set equality against live payloads.
 * Keep field order and names identical to the Python definitions.
 */

// -- src/podcast_reader/types.py:11 (StepName) --
export type StepName = 'resolve' | 'captions' | 'download' | 'transcribe' | 'chapters' | 'render'

// -- src/podcast_reader/types.py:12 (EventKind) --
export type EventKind = 'step_started' | 'step_finished' | 'warning' | 'job_done' | 'job_failed'

// -- src/podcast_reader/types.py:13 (JobState) --
export type JobState =
  | 'queued'
  | 'awaiting-confirmation'
  | 'running'
  | 'done'
  | 'failed'
  | 'interrupted'

// -- src/podcast_reader/types.py:15 (JOB_STATES) --
export const JOB_STATES: readonly JobState[] = [
  'queued',
  'awaiting-confirmation',
  'running',
  'done',
  'failed',
  'interrupted'
] as const

// -- src/podcast_reader/types.py:25 (PipelineEvent) --
export interface PipelineEvent {
  kind: EventKind
  step: StepName | null
  message: string
  data: Record<string, unknown>
}

// -- src/podcast_reader/types.py:32 (JobError) --
export interface JobError {
  code: string
  message: string
  hint: string
}

// -- src/podcast_reader/types.py:54 (PipelineResult) --
export interface PipelineResult {
  json_path: string
  chapters_path: string | null
  html_path: string
  title: string
}

// -- src/podcast_reader/types.py:61 (JobRecord) --
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

// -- src/podcast_reader/types.py:73 (LibraryEntry) --
export interface LibraryEntry {
  source_id: string
  source: string
  title: string
  html_path: string
  created_at: number
}

// -- src/podcast_reader/types.py:81 (EngineSettings) --
export interface EngineSettings {
  whisper_model: string
  whisper_lang: string
  whisper_device: string
  sentences: number
  library_dir: string
  chapter_model: string // "" means: the chapter provider's default model
  chapter_provider: string // a podcast_reader.providers.PROVIDERS key
  custom_provider_url: string // base URL for the "custom" provider ("" otherwise)
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

// -- src/podcast_reader/engine/app.py:137 (HealthInfo) --
export interface HealthInfo {
  version: string
  token_fingerprint: string
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
