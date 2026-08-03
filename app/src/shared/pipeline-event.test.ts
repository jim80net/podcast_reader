import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parsePipelineEvent } from './pipeline-event'
import { PIPELINE_EVENT_KINDS } from './types'
import type { PipelineEvent } from './types'

const validEvents = [
  { kind: 'step_started', step: 'resolve', message: '', data: { job_id: 'j1' } },
  {
    kind: 'step_progress',
    step: 'transcribe',
    message: '',
    data: { job_id: 'j1', seconds: 1.5, duration: null }
  },
  {
    kind: 'step_finished',
    step: 'render',
    message: '',
    data: { job_id: 'j1', caption_corrections: 2 }
  },
  {
    kind: 'warning',
    step: 'transcribe',
    message: 'using CPU',
    data: { job_id: 'j1', code: 'cuda_unavailable', reason: 'no GPU' }
  },
  { kind: 'job_done', step: null, message: 'Done', data: { job_id: 'j1' } },
  {
    kind: 'job_failed',
    step: null,
    message: 'failed',
    data: { job_id: 'j1', code: 'download_failed', hint: '', detail: '' }
  },
  {
    kind: 'pack_state',
    step: null,
    message: 'installed',
    data: { pack_id: 'model-small', state: 'installed' }
  },
  {
    kind: 'pack_progress',
    step: null,
    message: '',
    data: { pack_id: 'model-small', bytes: 1, total: 2 }
  },
  {
    kind: 'media_state',
    step: null,
    message: 'ready',
    data: { source_id: 'source-1', state: 'ready' }
  },
  {
    kind: 'media_progress',
    step: null,
    message: 'downloading',
    data: { source_id: 'source-1' }
  }
] as const satisfies readonly PipelineEvent[]

describe('PipelineEvent contract', () => {
  it('accepts every discriminated-union member', () => {
    expect(validEvents.map((event) => parsePipelineEvent(event)?.kind)).toEqual(
      PIPELINE_EVENT_KINDS
    )
  })

  it('rejects impossible identities, steps, fields, and value types', () => {
    const invalid: unknown[] = [
      { kind: 'step_started', step: null, message: '', data: { job_id: 'j1' } },
      { kind: 'step_started', step: 'resolve', message: '', data: {} },
      {
        kind: 'step_started',
        step: 'resolve',
        message: '',
        data: { job_id: 'j1', pack_id: 'pack-1' }
      },
      { kind: 'warning', step: 'resolve', message: '', data: { job_id: 'j1' } },
      { kind: 'job_done', step: 'resolve', message: '', data: { job_id: 'j1' } },
      {
        kind: 'pack_progress',
        step: null,
        message: '',
        data: { pack_id: 'pack-1', bytes: '1', total: 2 }
      },
      {
        kind: 'media_state',
        step: null,
        message: '',
        data: { source_id: 'source-1', state: 'ready', job_id: 'j1' }
      },
      {
        kind: 'media_progress',
        step: null,
        message: '',
        data: { source_id: 'source-1', future: true }
      },
      {
        kind: 'media_progress',
        step: null,
        message: '',
        data: { source_id: 'source-1' },
        future: true
      },
      { kind: 'future_event', step: null, message: '', data: {} }
    ]
    expect(invalid.map(parsePipelineEvent)).toEqual(invalid.map(() => null))
  })

  it('keeps Python and TypeScript union discriminants in exact parity', () => {
    const python = readFileSync(join(process.cwd(), '..', 'src', 'podcast_reader', 'types.py'), 'utf8')
    const block = /EventKind = Literal\[([\s\S]*?)\n\]/.exec(python)?.[1] ?? ''
    const pythonKinds = [...block.matchAll(/"([a-z_]+)"/g)].map((match) => match[1])
    expect(pythonKinds).toEqual(PIPELINE_EVENT_KINDS)
  })

  it('makes cross-family identity combinations fail the TypeScript typecheck', () => {
    const impossible: PipelineEvent = {
      kind: 'pack_state',
      step: null,
      message: '',
      // @ts-expect-error pack events cannot carry job identity.
      data: { pack_id: 'pack-1', job_id: 'j1', state: 'installed' }
    }
    expect(impossible.kind).toBe('pack_state')
  })
})
