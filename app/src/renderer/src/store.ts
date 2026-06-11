import { applyPipelineEvent, hydrateJobs, removeJob, upsertJob } from './jobs-store'
import type { JobsMap } from './jobs-store'
import type { EngineStatus } from '../../shared/ipc'
import type { JobRecord, PipelineEvent } from '../../shared/types'

/**
 * The renderer's single mutable state cell: engine status plus the job map,
 * with change notification for the views. All transitions delegate to the
 * pure reducers in jobs-store.ts (where the logic is unit-tested).
 */
export class AppStore {
  engine: EngineStatus = { state: 'starting' }
  jobs: JobsMap = new Map()
  private readonly listeners = new Set<() => void>()

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  setEngine(status: EngineStatus): void {
    this.engine = status
    this.notify()
  }

  hydrate(records: readonly JobRecord[]): void {
    this.jobs = hydrateJobs(records)
    this.notify()
  }

  /** Returns true when the event referenced a job we don't know → re-hydrate. */
  applyEvent(event: PipelineEvent): boolean {
    const result = applyPipelineEvent(this.jobs, event)
    if (result.known) {
      this.jobs = result.jobs
      this.notify()
    }
    return result.jobId !== null && !result.known
  }

  upsert(record: JobRecord): void {
    this.jobs = upsertJob(this.jobs, record)
    this.notify()
  }

  remove(jobId: string): void {
    this.jobs = removeJob(this.jobs, jobId)
    this.notify()
  }

  private notify(): void {
    for (const listener of this.listeners) listener()
  }
}

/** Views return their teardown so route changes never leak subscriptions. */
export type ViewCleanup = () => void
