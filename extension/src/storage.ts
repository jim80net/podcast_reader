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

const PAIRING_KEY = 'pairing'
const TRACKED_KEY = 'trackedJobs'

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
    if (typeof value !== 'object' || value === null) return null
    const pairing = value as Partial<Pairing>
    if (typeof pairing.port !== 'number' || typeof pairing.token !== 'string') return null
    return { port: pairing.port, token: pairing.token }
  }

  /** Store a verified pairing (callers verify via authed health first). */
  async setPairing(pairing: Pairing): Promise<void> {
    await this.area.set({ [PAIRING_KEY]: pairing })
  }

  async clearPairing(): Promise<void> {
    await this.area.remove(PAIRING_KEY)
  }

  async trackedJobs(): Promise<TrackedJob[]> {
    const items = await this.area.get([TRACKED_KEY])
    const value = items[TRACKED_KEY]
    return Array.isArray(value) ? (value as TrackedJob[]) : []
  }

  /** Prepend (most-recent-first), replacing any same-id entry, bounded. */
  async trackJob(job: TrackedJob): Promise<TrackedJob[]> {
    const current = await this.trackedJobs()
    const next = [job, ...current.filter((j) => j.id !== job.id)].slice(0, MAX_TRACKED_JOBS)
    await this.area.set({ [TRACKED_KEY]: next })
    return next
  }

  async setTrackedJobs(jobs: readonly TrackedJob[]): Promise<void> {
    await this.area.set({ [TRACKED_KEY]: jobs.slice(0, MAX_TRACKED_JOBS) })
  }

  async untrackJob(jobId: string): Promise<TrackedJob[]> {
    const next = (await this.trackedJobs()).filter((j) => j.id !== jobId)
    await this.area.set({ [TRACKED_KEY]: next })
    return next
  }
}

/** The production store, bound to the LOCAL area by construction. */
export function localStore(): ExtensionStore {
  return new ExtensionStore(chrome.storage.local)
}
