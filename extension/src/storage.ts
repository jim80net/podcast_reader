/**
 * Typed wrapper over `chrome.storage.local` (ext-pairing spec: the token
 * lives ONLY here and in popup/SW memory — never `storage.sync`, which would
 * ride Chrome Sync to other machines; the wrapper is constructed exclusively
 * from the local area so the type system has no door to sync). Tracked jobs
 * are a bounded most-recent-first list (ext-jobs spec).
 */

export interface Pairing {
  port: number
  token: string
}

export interface TrackedJob {
  id: string
  source: string
  title: string | null
  submitted_at: number
  /** True once a terminal-state notification fired (badge then clears). */
  notified: boolean
}

/** Most-recent-first bound on the tracked-job list. */
export const MAX_TRACKED_JOBS = 20
export const STORAGE_SCHEMA_VERSION = 1

const PAIRING_KEY = 'pairing'
const TRACKED_KEY = 'trackedJobs'
const MAX_TOKEN_LENGTH = 512
const MAX_JOB_ID_LENGTH = 128
const MAX_SOURCE_LENGTH = 8192
const MAX_TITLE_LENGTH = 4096
const TOKEN_PATTERN = /^[A-Za-z0-9_-]+$/
const PAIRING_KEYS = ['port', 'token'] as const
const PAIRING_ENVELOPE_KEYS = ['schema_version', 'port', 'token'] as const
const TRACKED_JOB_KEYS = ['id', 'source', 'title', 'submitted_at', 'notified'] as const
const TRACKED_ENVELOPE_KEYS = ['schema_version', 'jobs'] as const

/** The slice of `chrome.storage.local` the store needs (test seam). */
export interface KeyValueArea {
  get(keys: string[]): Promise<Record<string, unknown>>
  set(items: Record<string, unknown>): Promise<void>
  remove(keys: string | string[]): Promise<void>
}

export class ExtensionStore {
  constructor(private readonly area: KeyValueArea) {}

  async pairing(): Promise<Pairing | null> {
    const items = await this.area.get([PAIRING_KEY])
    const value = items[PAIRING_KEY]
    const current = parsePairingEnvelope(value)
    if (current !== null) return current
    const legacy = parsePairing(value)
    if (legacy === null) return null
    await this.area.set({ [PAIRING_KEY]: pairingEnvelope(legacy) })
    return legacy
  }

  /** Store a verified pairing (callers verify via authed health first). */
  async setPairing(pairing: Pairing): Promise<void> {
    const valid = parsePairing(pairing)
    if (valid === null) throw new TypeError('invalid extension pairing')
    await this.area.set({ [PAIRING_KEY]: pairingEnvelope(valid) })
  }

  async clearPairing(): Promise<void> {
    await this.area.remove(PAIRING_KEY)
  }

  async trackedJobs(): Promise<TrackedJob[]> {
    const items = await this.area.get([TRACKED_KEY])
    const value = items[TRACKED_KEY]
    const current = parseTrackedEnvelope(value)
    if (current !== null) {
      if (current.changed) await this.persistTrackedJobs(current.jobs)
      return current.jobs
    }
    if (!Array.isArray(value)) return []
    const legacy = normalizeTrackedJobs(value)
    await this.persistTrackedJobs(legacy.jobs)
    return legacy.jobs
  }

  /** Prepend (most-recent-first), replacing any same-id entry, bounded. */
  async trackJob(job: TrackedJob): Promise<TrackedJob[]> {
    const valid = parseTrackedJob(job)
    if (valid === null) throw new TypeError('invalid tracked job')
    const current = await this.trackedJobs()
    const next = [valid, ...current.filter((j) => j.id !== valid.id)].slice(0, MAX_TRACKED_JOBS)
    await this.persistTrackedJobs(next)
    return next
  }

  async setTrackedJobs(jobs: readonly TrackedJob[]): Promise<void> {
    const valid = jobs.map((job) => parseTrackedJob(job))
    if (valid.some((job) => job === null)) throw new TypeError('invalid tracked jobs')
    const normalized = normalizeTrackedJobs(valid)
    await this.persistTrackedJobs(normalized.jobs)
  }

  async untrackJob(jobId: string): Promise<TrackedJob[]> {
    const next = (await this.trackedJobs()).filter((j) => j.id !== jobId)
    await this.persistTrackedJobs(next)
    return next
  }

  private async persistTrackedJobs(jobs: readonly TrackedJob[]): Promise<void> {
    await this.area.set({
      [TRACKED_KEY]: { schema_version: STORAGE_SCHEMA_VERSION, jobs: jobs.slice(0, MAX_TRACKED_JOBS) }
    })
  }
}

type ObjectValue = Record<string, unknown>

function exactObject(value: unknown, keys: readonly string[]): ObjectValue | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const object = value as ObjectValue
  const actual = Object.keys(object)
  if (actual.length !== keys.length || actual.some((key) => !keys.includes(key))) return null
  return object
}

function parsePairing(value: unknown): Pairing | null {
  const object = exactObject(value, PAIRING_KEYS)
  if (object === null) return null
  if (!Number.isSafeInteger(object['port']) || Number(object['port']) < 1 || Number(object['port']) > 65535) return null
  if (typeof object['token'] !== 'string' || object['token'].length < 1 || object['token'].length > MAX_TOKEN_LENGTH || !TOKEN_PATTERN.test(object['token'])) return null
  return { port: Number(object['port']), token: object['token'] }
}

function parsePairingEnvelope(value: unknown): Pairing | null {
  const object = exactObject(value, PAIRING_ENVELOPE_KEYS)
  if (object === null || object['schema_version'] !== STORAGE_SCHEMA_VERSION) return null
  return parsePairing({ port: object['port'], token: object['token'] })
}

function pairingEnvelope(pairing: Pairing): ObjectValue {
  return { schema_version: STORAGE_SCHEMA_VERSION, port: pairing.port, token: pairing.token }
}

function parseTrackedJob(value: unknown): TrackedJob | null {
  const object = exactObject(value, TRACKED_JOB_KEYS)
  if (object === null) return null
  if (typeof object['id'] !== 'string' || object['id'].length < 1 || object['id'].length > MAX_JOB_ID_LENGTH) return null
  if (typeof object['source'] !== 'string' || object['source'].length < 1 || object['source'].length > MAX_SOURCE_LENGTH) return null
  if (object['title'] !== null && (typeof object['title'] !== 'string' || object['title'].length > MAX_TITLE_LENGTH)) return null
  if (typeof object['submitted_at'] !== 'number' || !Number.isFinite(object['submitted_at']) || object['submitted_at'] < 0) return null
  if (typeof object['notified'] !== 'boolean') return null
  return {
    id: object['id'],
    source: object['source'],
    title: object['title'],
    submitted_at: object['submitted_at'],
    notified: object['notified']
  }
}

function normalizeTrackedJobs(values: readonly unknown[]): { jobs: TrackedJob[]; changed: boolean } {
  const jobs: TrackedJob[] = []
  const ids = new Set<string>()
  for (const value of values) {
    const job = parseTrackedJob(value)
    if (job === null || ids.has(job.id)) continue
    ids.add(job.id)
    jobs.push(job)
    if (jobs.length === MAX_TRACKED_JOBS) break
  }
  return { jobs, changed: jobs.length !== values.length }
}

function parseTrackedEnvelope(value: unknown): { jobs: TrackedJob[]; changed: boolean } | null {
  const object = exactObject(value, TRACKED_ENVELOPE_KEYS)
  if (object === null || object['schema_version'] !== STORAGE_SCHEMA_VERSION || !Array.isArray(object['jobs'])) return null
  return normalizeTrackedJobs(object['jobs'])
}

/** The production store, bound to the LOCAL area by construction. */
export function localStore(): ExtensionStore {
  return new ExtensionStore(chrome.storage.local)
}
