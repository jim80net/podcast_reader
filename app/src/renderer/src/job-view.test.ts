import { describe, expect, it } from 'vitest'

import {
  deriveProgress,
  formatDate,
  sortJobs,
  sourceLabel,
  userFacingJobError,
  userFacingJobWarning
} from './job-view'
import type { JobRecord, PipelineEvent, StepName } from '../../shared/types'

const stepEvent = (
  kind: 'step_started' | 'step_finished',
  step: StepName,
  message = ''
): PipelineEvent => ({ kind, step, message, data: { job_id: 'j1' } })

describe('deriveProgress', () => {
  it('derives ordered steps with running/done status from events', () => {
    const events: PipelineEvent[] = [
      stepEvent('step_started', 'resolve', 'Resolving…'),
      stepEvent('step_finished', 'resolve', 'Video: T'),
      stepEvent('step_started', 'transcribe', 'Running whisper…')
    ]
    const progress = deriveProgress(events)
    expect(progress.steps).toEqual([
      { step: 'resolve', status: 'done', detail: 'Video: T', warnings: [] },
      { step: 'transcribe', status: 'running', detail: 'Running whisper…', warnings: [] }
    ])
  })

  it('replaces active copy when a step finishes without terminal copy', () => {
    const events: PipelineEvent[] = [
      stepEvent('step_started', 'captions', 'Fetching captions'),
      stepEvent('step_finished', 'captions')
    ]
    expect(deriveProgress(events).steps[0]?.detail).toBe('Captions loaded.')
  })

  it('replaces active copy with terminal copy when the job becomes done', () => {
    const started: PipelineEvent[] = [
      stepEvent('step_started', 'transcribe', 'Transcribing with whisper-worker...')
    ]
    expect(deriveProgress(started, 'running').steps[0]).toMatchObject({
      status: 'running',
      detail: 'Transcribing with whisper-worker...'
    })
    expect(deriveProgress([...started, stepEvent('step_finished', 'transcribe')], 'done').steps[0]).toMatchObject({
      status: 'done',
      detail: 'Transcription complete.'
    })
  })

  it('attaches warnings to their required pipeline step', () => {
    const events: PipelineEvent[] = [
      stepEvent('step_started', 'chapters'),
      {
        kind: 'warning',
        step: 'chapters',
        message: 'no API key',
        data: { job_id: 'j1', code: 'chapters_skipped' }
      }
    ]
    const progress = deriveProgress(events)
    expect(progress.steps[0]?.warnings).toEqual(['no API key'])
  })

  it('ignores job_done / job_failed events (states come from the record)', () => {
    const events: PipelineEvent[] = [
      { kind: 'job_done', step: null, message: 'Done', data: { job_id: 'j1' } },
      {
        kind: 'job_failed',
        step: null,
        message: 'boom',
        data: { job_id: 'j1', code: 'failed', hint: '', detail: '' }
      }
    ]
    expect(deriveProgress(events)).toEqual({ steps: [] })
  })
})

describe('userFacingJobError', () => {
  it('leads with cause and recovery while preserving raw diagnostics', () => {
    const view = userFacingJobError({
      code: 'captions_unavailable',
      message: 'Could not retrieve a transcript for the video.',
      hint: '',
      detail: 'Transcript endpoint returned HTTP 404'
    })
    expect(view.cause).toBe('This video is unavailable or does not provide captions.')
    expect(view.recovery).toBe('Check that the video is public and has captions, then try again.')
    expect(view.technicalDetail).toContain('Code: captions_unavailable')
    expect(view.technicalDetail).toContain('Could not retrieve a transcript')
    expect(view.technicalDetail).toContain('HTTP 404')
  })

  it('uses a generic cause instead of exposing unknown third-party text', () => {
    const view = userFacingJobError({
      code: 'provider_failed',
      message: 'opaque vendor text',
      hint: '',
      detail: ''
    })
    expect(view.cause).toBe('The job could not be completed.')
    expect(view.technicalDetail).toContain('opaque vendor text')
  })
})

describe('userFacingJobWarning', () => {
  it('translates missing chapter configuration while retaining diagnostic detail', () => {
    expect(userFacingJobWarning('ANTHROPIC_API_KEY is not set; chapters skipped')).toEqual({
      message: 'Chapters are off. Add a chapter provider in Settings to enable them.',
      technicalDetail: 'ANTHROPIC_API_KEY is not set; chapters skipped'
    })
  })

  it('leaves unrelated warnings unchanged', () => {
    expect(userFacingJobWarning('Audio was clipped')).toEqual({
      message: 'Audio was clipped',
      technicalDetail: null
    })
  })
})

describe('sortJobs', () => {
  it('orders newest-created first without mutating its input', () => {
    const a = { id: 'a', created_at: 1 } as JobRecord
    const b = { id: 'b', created_at: 2 } as JobRecord
    const input = [a, b]
    expect(sortJobs(input).map((j) => j.id)).toEqual(['b', 'a'])
    expect(input.map((j) => j.id)).toEqual(['a', 'b'])
  })
})

describe('sourceLabel', () => {
  it('shows the host and path for URLs', () => {
    expect(sourceLabel('https://www.youtube.com/watch?v=abc')).toBe('www.youtube.com/watch')
    expect(sourceLabel('https://x.com/user/status/1')).toBe('x.com/user/status/1')
  })

  it('shows the basename for local paths', () => {
    expect(sourceLabel('/home/test-user/Downloads/episode.mp3')).toBe('episode.mp3')
    expect(sourceLabel('C:\\Users\\test-user\\episode.mp3')).toBe('episode.mp3')
  })
})

describe('formatDate', () => {
  it('renders an epoch-seconds timestamp as a local date string', () => {
    const text = formatDate(1742860800) // 2025-03-25T00:00:00Z
    expect(text).toMatch(/2025/)
  })
})
