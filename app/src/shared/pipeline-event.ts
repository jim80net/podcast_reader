import type { PackInstallError, PipelineEvent, StepName } from './types'

const STEPS = new Set<StepName>([
  'resolve',
  'captions',
  'download',
  'transcribe',
  'diarize',
  'chapters',
  'render'
])
const PACK_STATES = new Set([
  'not-installed',
  'resumable',
  'installing',
  'installed',
  'incompatible',
  'failed',
  'unavailable'
])
const MEDIA_STATES = new Set(['ready', 'preparing', 'unavailable'])

type JsonObject = Record<string, unknown>

const isObject = (value: unknown): value is JsonObject =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const hasExactKeys = (value: JsonObject, required: readonly string[], optional: readonly string[] = []): boolean => {
  const keys = Object.keys(value)
  return required.every((key) => key in value) &&
    keys.every((key) => required.includes(key) || optional.includes(key))
}

const isNonBlank = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

const isNonNegativeInteger = (value: unknown): value is number =>
  Number.isSafeInteger(value) && (value as number) >= 0

const isStep = (value: unknown): value is StepName =>
  typeof value === 'string' && STEPS.has(value as StepName)

const isPackError = (value: unknown): value is PackInstallError =>
  isObject(value) && hasExactKeys(value, ['code', 'message']) &&
  isNonBlank(value['code']) && typeof value['message'] === 'string'

const hasJobIdentity = (data: JsonObject): boolean => isNonBlank(data['job_id'])
const hasPackIdentity = (data: JsonObject): boolean => isNonBlank(data['pack_id'])
const hasMediaIdentity = (data: JsonObject): boolean => isNonBlank(data['source_id'])

/** Validate one untrusted SSE payload before it enters typed main/renderer code. */
export function parsePipelineEvent(value: unknown): PipelineEvent | null {
  if (!isObject(value) || !hasExactKeys(value, ['kind', 'step', 'message', 'data'])) return null
  if (typeof value['message'] !== 'string' || !isObject(value['data'])) return null
  const data = value['data']

  switch (value['kind']) {
    case 'step_started':
      if (!isStep(value['step']) || !hasExactKeys(data, ['job_id'], ['cached'])) return null
      if (!hasJobIdentity(data) || (data['cached'] !== undefined && typeof data['cached'] !== 'boolean')) return null
      break
    case 'step_progress':
      if (!isStep(value['step']) || !hasExactKeys(data, ['job_id', 'seconds', 'duration'])) return null
      if (!hasJobIdentity(data) || !isFiniteNumber(data['seconds'])) return null
      if (data['duration'] !== null && !isFiniteNumber(data['duration'])) return null
      break
    case 'step_finished':
      if (!isStep(value['step']) || !hasExactKeys(data, ['job_id'], ['cached', 'caption_corrections'])) return null
      if (!hasJobIdentity(data)) return null
      if (data['cached'] !== undefined && typeof data['cached'] !== 'boolean') return null
      if (data['caption_corrections'] !== undefined && !isNonNegativeInteger(data['caption_corrections'])) return null
      break
    case 'warning':
      if (!isStep(value['step']) || !hasExactKeys(data, ['job_id', 'code'], ['reason'])) return null
      if (!hasJobIdentity(data) || !isNonBlank(data['code'])) return null
      if (data['reason'] !== undefined && typeof data['reason'] !== 'string') return null
      break
    case 'job_done':
      if (value['step'] !== null || !hasExactKeys(data, ['job_id']) || !hasJobIdentity(data)) return null
      break
    case 'job_failed':
      if (value['step'] !== null || !hasExactKeys(data, ['job_id', 'code', 'hint', 'detail'])) return null
      if (!hasJobIdentity(data) || !isNonBlank(data['code'])) return null
      if (typeof data['hint'] !== 'string' || typeof data['detail'] !== 'string') return null
      break
    case 'pack_state':
      if (value['step'] !== null || !hasExactKeys(data, ['pack_id', 'state'], ['error'])) return null
      if (!hasPackIdentity(data) || typeof data['state'] !== 'string' || !PACK_STATES.has(data['state'])) return null
      if (data['error'] !== undefined && !isPackError(data['error'])) return null
      break
    case 'pack_progress':
      if (value['step'] !== null || !hasExactKeys(data, ['pack_id', 'bytes', 'total'])) return null
      if (!hasPackIdentity(data) || !isNonNegativeInteger(data['bytes']) || !isNonNegativeInteger(data['total'])) return null
      break
    case 'media_state':
      if (value['step'] !== null || !hasExactKeys(data, ['source_id', 'state'])) return null
      if (!hasMediaIdentity(data) || typeof data['state'] !== 'string' || !MEDIA_STATES.has(data['state'])) return null
      break
    case 'media_progress':
      if (value['step'] !== null || !hasExactKeys(data, ['source_id']) || !hasMediaIdentity(data)) return null
      break
    default:
      return null
  }

  return value as unknown as PipelineEvent
}
