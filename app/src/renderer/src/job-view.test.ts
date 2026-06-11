import { describe, expect, it } from 'vitest'

import { deriveProgress, formatDate, sortJobs, sourceLabel } from './job-view'
import type { JobRecord, PipelineEvent } from '../../shared/types'

function event(overrides: Partial<PipelineEvent>): PipelineEvent {
  return { kind: 'step_started', step: 'resolve', message: '', data: {}, ...overrides }
}

describe('deriveProgress', () => {
  it('derives ordered steps with running/done status from events', () => {
    const events: PipelineEvent[] = [
      event({ kind: 'step_started', step: 'resolve', message: 'Resolving…' }),
      event({ kind: 'step_finished', step: 'resolve', message: 'Video: T' }),
      event({ kind: 'step_started', step: 'transcribe', message: 'Running whisper…' })
    ]
    const progress = deriveProgress(events)
    expect(progress.steps).toEqual([
      { step: 'resolve', status: 'done', detail: 'Video: T', warnings: [] },
      { step: 'transcribe', status: 'running', detail: 'Running whisper…', warnings: [] }
    ])
  })

  it('keeps the last non-empty message as the step detail', () => {
    const events: PipelineEvent[] = [
      event({ kind: 'step_started', step: 'captions', message: 'Fetching captions' }),
      event({ kind: 'step_finished', step: 'captions', message: '' })
    ]
    expect(deriveProgress(events).steps[0]?.detail).toBe('Fetching captions')
  })

  it('attaches warnings to their step and collects step-less warnings', () => {
    const events: PipelineEvent[] = [
      event({ kind: 'step_started', step: 'chapters' }),
      event({ kind: 'warning', step: 'chapters', message: 'no API key' }),
      event({ kind: 'warning', step: null, message: 'global warning' })
    ]
    const progress = deriveProgress(events)
    expect(progress.steps[0]?.warnings).toEqual(['no API key'])
    expect(progress.warnings).toEqual(['global warning'])
  })

  it('ignores job_done / job_failed events (states come from the record)', () => {
    const events: PipelineEvent[] = [
      event({ kind: 'job_done', step: null, message: 'Done' }),
      event({ kind: 'job_failed', step: null, message: 'boom' })
    ]
    expect(deriveProgress(events)).toEqual({ steps: [], warnings: [] })
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
    expect(sourceLabel('/home/jim/Downloads/episode.mp3')).toBe('episode.mp3')
    expect(sourceLabel('C:\\Users\\jim\\episode.mp3')).toBe('episode.mp3')
  })
})

describe('formatDate', () => {
  it('renders an epoch-seconds timestamp as a local date string', () => {
    const text = formatDate(1742860800) // 2025-03-25T00:00:00Z
    expect(text).toMatch(/2025/)
  })
})
