import { LatestGate } from './latest-gate'
import type { JobRecord } from '../../shared/types'

/**
 * Serializes job re-hydration so out-of-order async completions can never
 * regress the store: `refresh()` fires from several triggers (initial load,
 * an event for an unknown job), and only the latest request's response is
 * applied. A main-process hydration push (`applyPush`) is by definition
 * fresher than any fetch still in flight, so it also invalidates them.
 */

export interface JobsHydrationStore {
  hydrate(records: readonly JobRecord[]): void
}

export interface JobsHydrator {
  /** Fetch the job records and hydrate the store, unless superseded. */
  refresh(): Promise<void>
  /** Apply a pushed hydration, invalidating any fetch still in flight. */
  applyPush(records: readonly JobRecord[]): void
}

export function createJobsHydrator(
  listJobs: () => Promise<JobRecord[]>,
  store: JobsHydrationStore
): JobsHydrator {
  const gate = new LatestGate()
  return {
    async refresh(): Promise<void> {
      const isLatest = gate.next()
      let records: JobRecord[]
      try {
        records = await listJobs()
      } catch {
        return // engine not ready yet — the hydration push follows
      }
      if (isLatest()) store.hydrate(records)
    },
    applyPush(records: readonly JobRecord[]): void {
      gate.next()
      store.hydrate(records)
    }
  }
}
