import { describe, expect, it } from 'vitest'

import { applyEvent, badgeForJobs, evaluatePoll, isTerminal, viewFromRecord } from './jobs-view'
import type { PolledRecord } from './jobs-view'
import type { TrackedJob } from './storage'
import type { JobRecord, JobState, PipelineEvent } from '../../app/src/shared/types'

function record(id: string, state: JobState, overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id,
    source: `https://e.com/${id}`,
    title: null,
    state,
    error: null,
    events: [],
    result: null,
    overrides: null,
    models: null,
    created_at: 1,
    updated_at: 1,
    ...overrides
  }
}

function event(jobId: string | undefined, kind: PipelineEvent['kind']): PipelineEvent {
  return {
    kind,
    step: 'download',
    message: 'downloading',
    data: jobId === undefined ? {} : { job_id: jobId }
  }
}

function tracked(id: string, overrides: Partial<TrackedJob> = {}): TrackedJob {
  return { id, source: `https://e.com/${id}`, title: null, submitted_at: 1, notified: false, ...overrides }
}

describe('isTerminal', () => {
  it('marks done/failed/interrupted terminal and the rest live', () => {
    expect(isTerminal('done')).toBe(true)
    expect(isTerminal('failed')).toBe(true)
    expect(isTerminal('interrupted')).toBe(true)
    expect(isTerminal('queued')).toBe(false)
    expect(isTerminal('awaiting-confirmation')).toBe(false)
    expect(isTerminal('running')).toBe(false)
  })
})

describe('applyEvent (hydrate-then-stream merge)', () => {
  const base = new Map([['j1', viewFromRecord(record('j1', 'queued'))]])

  it('overlays live step/message on a tracked job and bumps queued → running', () => {
    const { views, refreshJobId } = applyEvent(base, event('j1', 'step_started'))
    expect(refreshJobId).toBeNull()
    expect(views.get('j1')).toMatchObject({
      liveStep: 'download',
      liveMessage: 'downloading',
      record: { state: 'running' }
    })
  })

  it('requests a record refresh on terminal events instead of mutating state', () => {
    const { views, refreshJobId } = applyEvent(base, event('j1', 'job_done'))
    expect(refreshJobId).toBe('j1')
    expect(views.get('j1')?.record.state).toBe('queued') // record stays authoritative
  })

  it('ignores events for untracked jobs and events without job_id (pack events, per Q5)', () => {
    expect(applyEvent(base, event('other', 'step_started')).views.get('j1')?.liveStep).toBeNull()
    expect(applyEvent(base, event(undefined, 'pack_state')).refreshJobId).toBeNull()
  })

  it('never mutates the input map', () => {
    applyEvent(base, event('j1', 'step_started'))
    expect(base.get('j1')?.liveStep).toBeNull()
  })
})

describe('badgeForJobs (state-only, per review adjudication)', () => {
  it('prioritizes running > queued > unnotified failure > unnotified done > clear', () => {
    expect(badgeForJobs([{ state: 'running', notified: false }]).text).toBe('RUN')
    expect(
      badgeForJobs([
        { state: 'running', notified: false },
        { state: 'failed', notified: false }
      ]).text
    ).toBe('RUN')
    expect(badgeForJobs([{ state: 'queued', notified: false }]).text).toBe('QUE')
    expect(badgeForJobs([{ state: 'failed', notified: false }]).text).toBe('ERR')
    expect(badgeForJobs([{ state: 'interrupted', notified: false }]).text).toBe('ERR')
    expect(badgeForJobs([{ state: 'done', notified: false }]).text).toBe('OK')
  })

  it('clears once terminal states are notified', () => {
    expect(badgeForJobs([{ state: 'done', notified: true }]).text).toBe('')
    expect(badgeForJobs([{ state: 'failed', notified: true }]).text).toBe('')
    expect(badgeForJobs([]).text).toBe('')
  })
})

describe('evaluatePoll (stateless alarm wake)', () => {
  it('notifies once per terminal job and clears the alarm when nothing is live', () => {
    const records = new Map<string, PolledRecord>([
      ['j1', record('j1', 'done', { title: 'Episode 1' })]
    ])
    const outcome = evaluatePoll([tracked('j1')], records)
    expect(outcome.notifications).toEqual([
      { jobId: 'j1', title: 'Transcript ready', message: 'Episode 1' }
    ])
    expect(outcome.tracked[0]?.notified).toBe(true)
    expect(outcome.keepAlarm).toBe(false)
  })

  it('does not re-notify already-notified jobs (SW restarts stay idempotent)', () => {
    const records = new Map<string, PolledRecord>([['j1', record('j1', 'done')]])
    const outcome = evaluatePoll([tracked('j1', { notified: true })], records)
    expect(outcome.notifications).toEqual([])
  })

  it('keeps the alarm while any job is non-terminal', () => {
    const records = new Map<string, PolledRecord>([
      ['j1', record('j1', 'running')],
      ['j2', record('j2', 'done')]
    ])
    const outcome = evaluatePoll([tracked('j1'), tracked('j2')], records)
    expect(outcome.keepAlarm).toBe(true)
    expect(outcome.notifications).toHaveLength(1)
    expect(outcome.badge.text).toBe('RUN')
  })

  it('keeps the alarm and the job on unreachable fetches (engine may return)', () => {
    const outcome = evaluatePoll([tracked('j1')], new Map([['j1', 'unreachable' as const]]))
    expect(outcome.keepAlarm).toBe(true)
    expect(outcome.tracked).toHaveLength(1)
    expect(outcome.notifications).toEqual([])
  })

  it('drops 404 records from tracking', () => {
    const outcome = evaluatePoll([tracked('j1')], new Map([['j1', 'missing' as const]]))
    expect(outcome.tracked).toEqual([])
    expect(outcome.keepAlarm).toBe(false)
  })

  it('carries the failure message into the notification', () => {
    const failed = record('j1', 'failed', {
      error: {
        code: 'download_auth_required',
        message: 'needs login',
        hint: 'share it',
        detail: 'ERROR: needs login'
      }
    })
    const outcome = evaluatePoll([tracked('j1')], new Map<string, PolledRecord>([['j1', failed]]))
    expect(outcome.notifications[0]).toEqual({
      jobId: 'j1',
      title: 'Transcription failed',
      message: 'https://e.com/j1 — needs login'
    })
  })
})
