import type { JobRecord, PipelineEvent } from '../../shared/types'

/**
 * Renderer-side job state: pure reducers over an immutable id → record map.
 *
 * Job records are the source of truth and arrive via hydration
 * (`jobs:hydrated` after every SSE (re)connect); forwarded `engine:event`
 * pushes are an optimization layered on top (design decision 4). An event for
 * a job we don't know flags `known: false` so the caller can re-fetch records
 * instead of guessing.
 */

export type JobsMap = ReadonlyMap<string, JobRecord>

export interface ApplyResult {
  jobs: JobsMap
  /** The event's job id, or null when the event carries none. */
  jobId: string | null
  /** False when the job is unknown locally — caller should re-hydrate. */
  known: boolean
}

export function hydrateJobs(records: readonly JobRecord[]): JobsMap {
  return new Map(records.map((record) => [record.id, record]))
}

export function upsertJob(jobs: JobsMap, record: JobRecord): JobsMap {
  const next = new Map(jobs)
  next.set(record.id, record)
  return next
}

export function removeJob(jobs: JobsMap, jobId: string): JobsMap {
  const next = new Map(jobs)
  next.delete(jobId)
  return next
}

export function applyPipelineEvent(jobs: JobsMap, event: PipelineEvent): ApplyResult {
  const jobId = event.data['job_id']
  if (typeof jobId !== 'string') return { jobs, jobId: null, known: false }
  const record = jobs.get(jobId)
  if (record === undefined) return { jobs, jobId, known: false }
  const updated: JobRecord = {
    ...record,
    events: [...record.events, event],
    state: nextState(record, event),
    error: nextError(record, event)
  }
  return { jobs: upsertJob(jobs, updated), jobId, known: true }
}

function nextState(record: JobRecord, event: PipelineEvent): JobRecord['state'] {
  if (event.kind === 'job_done') return 'done'
  if (event.kind === 'job_failed') return 'failed'
  // A step event means the worker picked the job up; any other state (incl.
  // terminal ones the records already established) is never regressed.
  if (record.state === 'queued') return 'running'
  return record.state
}

function nextError(record: JobRecord, event: PipelineEvent): JobRecord['error'] {
  if (event.kind !== 'job_failed') return record.error
  return {
    code: typeof event.data['code'] === 'string' ? event.data['code'] : '',
    message: event.message,
    hint: typeof event.data['hint'] === 'string' ? event.data['hint'] : ''
  }
}
