import { describe, expect, it } from 'vitest'

import { applyPipelineEvent, hydrateJobs, removeJob, upsertJob } from './jobs-store'
import type { JobRecord, PipelineEvent } from '../../shared/types'

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: 'j1',
    source: 'https://example.com/v',
    title: null,
    state: 'queued',
    error: null,
    events: [],
    result: null,
    overrides: null,
    models: null,
    created_at: 100,
    updated_at: 100,
    ...overrides
  }
}

const started = (jobId = 'j1', message = ''): PipelineEvent => ({
  kind: 'step_started',
  step: 'resolve',
  message,
  data: { job_id: jobId }
})

const finished = (): PipelineEvent => ({
  kind: 'step_finished',
  step: 'resolve',
  message: '',
  data: { job_id: 'j1' }
})

describe('hydrateJobs', () => {
  it('replaces all records (records are the source of truth)', () => {
    const before = hydrateJobs([job({ id: 'old', state: 'running' })])
    const after = hydrateJobs([job({ id: 'j1' }), job({ id: 'j2' })])
    expect([...after.keys()]).toEqual(['j1', 'j2'])
    expect(before.has('old')).toBe(true) // the old map is untouched
  })
})

describe('applyPipelineEvent', () => {
  it('appends the event to the matching job', () => {
    const jobs = hydrateJobs([job()])
    const { jobs: next, jobId } = applyPipelineEvent(jobs, started('j1', 'go'))
    expect(jobId).toBe('j1')
    expect(next.get('j1')?.events).toHaveLength(1)
    expect(jobs.get('j1')?.events).toHaveLength(0) // input not mutated
  })

  it('moves a queued job to running on its first step event', () => {
    const jobs = hydrateJobs([job()])
    const { jobs: next } = applyPipelineEvent(jobs, started())
    expect(next.get('j1')?.state).toBe('running')
    const result = applyPipelineEvent(jobs, finished())
    expect(result.jobs.get('j1')?.state).toBe('running')
  })

  it('does not infer running from a warning — only step events mean pickup', () => {
    const jobs = hydrateJobs([job()])
    const { jobs: next } = applyPipelineEvent(
      jobs,
      {
        kind: 'warning',
        step: 'chapters',
        message: 'chapters skipped',
        data: { job_id: 'j1', code: 'chapters_skipped' }
      }
    )
    expect(next.get('j1')?.state).toBe('queued')
    expect(next.get('j1')?.events).toHaveLength(1) // still recorded on the timeline
  })

  it('marks the job done on job_done', () => {
    const jobs = hydrateJobs([job({ state: 'running' })])
    const { jobs: next } = applyPipelineEvent(
      jobs,
      { kind: 'job_done', step: null, message: 'Done', data: { job_id: 'j1' } }
    )
    expect(next.get('j1')?.state).toBe('done')
  })

  it('marks the job failed with the structured error on job_failed', () => {
    const jobs = hydrateJobs([job({ state: 'running' })])
    const { jobs: next } = applyPipelineEvent(
      jobs,
      {
        kind: 'job_failed',
        step: null,
        message: 'download failed',
        data: { job_id: 'j1', code: 'download', hint: 'check the URL', detail: 'stderr...' }
      }
    )
    const record = next.get('j1')
    expect(record?.state).toBe('failed')
    expect(record?.error).toEqual({
      code: 'download',
      message: 'download failed',
      hint: 'check the URL',
      detail: 'stderr...'
    })
  })

  it('reports unknown jobs so the caller can re-hydrate', () => {
    const jobs = hydrateJobs([])
    const result = applyPipelineEvent(jobs, started())
    expect(result.known).toBe(false)
    expect(result.jobs).toBe(jobs)
  })

  it('ignores non-job routed events', () => {
    const jobs = hydrateJobs([job()])
    const result = applyPipelineEvent(jobs, {
      kind: 'media_progress',
      step: null,
      message: '',
      data: { source_id: 'source-1' }
    })
    expect(result.jobId).toBeNull()
    expect(result.jobs).toBe(jobs)
  })

  it('does not regress a terminal state on a late step event', () => {
    const jobs = hydrateJobs([job({ state: 'done' })])
    const { jobs: next } = applyPipelineEvent(jobs, finished())
    expect(next.get('j1')?.state).toBe('done')
  })
})

describe('upsertJob / removeJob', () => {
  it('inserts and replaces records by id', () => {
    let jobs = hydrateJobs([])
    jobs = upsertJob(jobs, job())
    jobs = upsertJob(jobs, job({ state: 'running' }))
    expect(jobs.size).toBe(1)
    expect(jobs.get('j1')?.state).toBe('running')
  })

  it('removes records by id', () => {
    const jobs = upsertJob(hydrateJobs([]), job())
    const next = removeJob(jobs, 'j1')
    expect(next.has('j1')).toBe(false)
    expect(jobs.has('j1')).toBe(true) // input not mutated
  })
})
