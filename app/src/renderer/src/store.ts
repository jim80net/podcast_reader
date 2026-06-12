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
  private engineStatus: EngineStatus = { state: 'starting' }
  private jobsMap: JobsMap = new Map()
  private readonly listeners = new Set<() => void>()

  /** Read-only views: every mutation goes through a mutator so notify() always fires. */
  get engine(): EngineStatus {
    return this.engineStatus
  }

  get jobs(): JobsMap {
    return this.jobsMap
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  setEngine(status: EngineStatus): void {
    this.engineStatus = status
    this.notify()
  }

  hydrate(records: readonly JobRecord[]): void {
    this.jobsMap = hydrateJobs(records)
    this.notify()
  }

  /** Returns true when the event referenced a job we don't know → re-hydrate. */
  applyEvent(event: PipelineEvent): boolean {
    const result = applyPipelineEvent(this.jobsMap, event)
    if (result.known) {
      this.jobsMap = result.jobs
      this.notify()
    }
    return result.jobId !== null && !result.known
  }

  upsert(record: JobRecord): void {
    this.jobsMap = upsertJob(this.jobsMap, record)
    this.notify()
  }

  remove(jobId: string): void {
    this.jobsMap = removeJob(this.jobsMap, jobId)
    this.notify()
  }

  private notify(): void {
    for (const listener of this.listeners) listener()
  }
}

/** Views return their teardown so route changes never leak subscriptions. */
export type ViewCleanup = () => void
