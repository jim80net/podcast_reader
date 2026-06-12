import type {
  HardwareInfo,
  PackInstallError,
  PackState,
  PackStatus,
  PipelineEvent
} from '../../shared/types'

/**
 * Renderer-side pack state: pure reducers and predicates over the
 * `GET /v1/packs` payload (the hydration source of truth) patched by
 * forwarded `pack_progress` / `pack_state` events. Pack events carry
 * `data.pack_id` and never `job_id` (per Q5), so they are disjoint from the
 * job reducers by construction. Used by the setup wizard and the Settings
 * Packs section; all logic lives here so it is unit-testable.
 */

export interface PackEventResult {
  packs: readonly PackStatus[]
  /** False when the event is not pack-shaped (job events pass through untouched). */
  isPackEvent: boolean
  /** True when authoritative state must be re-fetched from `GET /v1/packs`. */
  needsRefresh: boolean
}

export function applyPackEvent(
  packs: readonly PackStatus[],
  event: PipelineEvent
): PackEventResult {
  if (event.kind !== 'pack_progress' && event.kind !== 'pack_state') {
    return { packs, isPackEvent: false, needsRefresh: false }
  }
  const packId = event.data['pack_id']
  const index = packs.findIndex((pack) => pack.id === packId)
  if (typeof packId !== 'string' || index === -1) {
    // An event for a pack we don't know: the listing is the truth — re-fetch.
    return { packs, isPackEvent: true, needsRefresh: true }
  }
  const current = packs[index] as PackStatus
  if (event.kind === 'pack_progress') {
    const bytes = event.data['bytes']
    const total = event.data['total']
    if (typeof bytes !== 'number' || typeof total !== 'number') {
      return { packs, isPackEvent: true, needsRefresh: true }
    }
    const next = [...packs]
    next[index] = { ...current, state: 'installing', progress: { bytes, total } }
    return { packs: next, isPackEvent: true, needsRefresh: false }
  }
  // pack_state: patch what the event carries for immediate feedback, then
  // re-hydrate — fields the event omits (installed_version) need the listing.
  const state = event.data['state']
  if (!isPackState(state)) return { packs, isPackEvent: true, needsRefresh: true }
  const error = event.data['error']
  const next = [...packs]
  next[index] = {
    ...current,
    state,
    progress: state === 'installing' ? current.progress : null,
    error: isPackInstallError(error) ? error : null
  }
  return { packs: next, isPackEvent: true, needsRefresh: true }
}

const PACK_STATES: readonly PackState[] = [
  'not-installed',
  'resumable',
  'installing',
  'installed',
  'incompatible',
  'failed',
  'unavailable'
]

function isPackState(value: unknown): value is PackState {
  return typeof value === 'string' && (PACK_STATES as readonly string[]).includes(value)
}

function isPackInstallError(value: unknown): value is PackInstallError {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { code?: unknown }).code === 'string' &&
    typeof (value as { message?: unknown }).message === 'string'
  )
}

/** States from which an install request makes sense (incl. the per-S8 re-download). */
export function installableNow(state: PackState): boolean {
  return (
    state === 'not-installed' ||
    state === 'resumable' ||
    state === 'failed' ||
    state === 'incompatible'
  )
}

/** A recommended pack that is neither usable nor on its way counts as missing. */
function recommendedMissing(pack: PackStatus): boolean {
  return pack.recommended && installableNow(pack.state)
}

/** First-run wizard trigger (app-setup-ui spec): any recommended pack missing. */
export function setupNeeded(packs: readonly PackStatus[]): boolean {
  return packs.some(recommendedMissing)
}

/** Wizard pre-selection: recommended packs that still need installing. */
export function defaultSelection(packs: readonly PackStatus[]): Set<string> {
  return new Set(packs.filter(recommendedMissing).map((pack) => pack.id))
}

/**
 * Device defaulting (per S4): cuda iff Windows + NVIDIA GPU with the CUDA
 * pack registry-available (any state but `unavailable`), else cpu.
 */
export function deriveWhisperDevice(
  hardware: HardwareInfo,
  packs: readonly PackStatus[]
): 'cuda' | 'cpu' {
  const cudaAvailable = packs.some(
    (pack) => pack.id === 'cuda-runtime' && pack.state !== 'unavailable'
  )
  return hardware.platform === 'win32' && hardware.nvidia_gpu && cudaAvailable ? 'cuda' : 'cpu'
}

/**
 * Settings advisory (per S4/Q2): `whisper_device=cuda` with no usable CUDA
 * pack — not installed, resumable, installing, incompatible, or failed. On
 * platforms where the pack is registry-`unavailable` there is nothing to
 * install, so no advisory (mirrors the engine's warning suppression).
 */
export function cudaAdvisoryNeeded(device: string, packs: readonly PackStatus[]): boolean {
  if (device !== 'cuda') return false
  const cuda = packs.find((pack) => pack.id === 'cuda-runtime')
  if (cuda === undefined) return false
  return cuda.state !== 'installed' && cuda.state !== 'unavailable'
}

/** True once every selected pack reports installed (wizard completion gate). */
export function selectionInstalled(
  packs: readonly PackStatus[],
  selection: ReadonlySet<string>
): boolean {
  if (selection.size === 0) return false
  return [...selection].every(
    (id) => packs.find((pack) => pack.id === id)?.state === 'installed'
  )
}

/** Human download size, decimal units (matches how hosts advertise sizes). */
export function formatBytes(size: number): string {
  if (size >= 1e9) return `${(size / 1e9).toFixed(1)} GB`
  if (size >= 1e6) return `${(size / 1e6).toFixed(0)} MB`
  if (size >= 1e3) return `${(size / 1e3).toFixed(0)} kB`
  return `${size} B`
}

const PLATFORM_LABELS: Record<string, string> = {
  win32: 'Windows',
  darwin: 'macOS',
  linux: 'Linux'
}

/** One-line hardware summary for the wizard header. */
export function hardwareSummary(hardware: HardwareInfo): string {
  const platform = PLATFORM_LABELS[hardware.platform] ?? hardware.platform
  if (!hardware.nvidia_gpu) return `${platform} — no NVIDIA GPU detected`
  const names = hardware.gpu_names.join(', ')
  return `${platform} — NVIDIA GPU${names === '' ? ' detected' : `: ${names}`}`
}

/** Percentage (0–100) for a progress bar; null progress or zero total → 0. */
export function progressPercent(progress: { bytes: number; total: number } | null): number {
  if (progress === null || progress.total <= 0) return 0
  return Math.min(100, Math.round((progress.bytes / progress.total) * 100))
}
