import { describe, expect, it } from 'vitest'

import { isMediaReady, mediaTerminalState } from './media-events'
import type { PipelineEvent } from '../../shared/types'

function event(overrides: Partial<PipelineEvent>): PipelineEvent {
  return { kind: 'media_state', step: null, message: '', data: {}, ...overrides }
}

describe('isMediaReady', () => {
  it('matches a media_state ready event for the same source_id', () => {
    expect(isMediaReady(event({ data: { source_id: 'abc', state: 'ready' } }), 'abc')).toBe(true)
  })

  it('ignores other source ids, other states, and other kinds', () => {
    expect(isMediaReady(event({ data: { source_id: 'other', state: 'ready' } }), 'abc')).toBe(false)
    expect(isMediaReady(event({ data: { source_id: 'abc', state: 'preparing' } }), 'abc')).toBe(
      false
    )
    expect(
      isMediaReady(event({ kind: 'pack_state', data: { source_id: 'abc', state: 'ready' } }), 'abc')
    ).toBe(false)
  })
})

describe('mediaTerminalState', () => {
  it('returns the terminal state for the matching source', () => {
    expect(mediaTerminalState(event({ data: { source_id: 'abc', state: 'ready' } }), 'abc')).toBe(
      'ready'
    )
    expect(
      mediaTerminalState(event({ data: { source_id: 'abc', state: 'unavailable' } }), 'abc')
    ).toBe('unavailable')
  })

  it('returns null for non-terminal states, other sources, and other kinds', () => {
    expect(
      mediaTerminalState(event({ data: { source_id: 'abc', state: 'preparing' } }), 'abc')
    ).toBeNull()
    expect(
      mediaTerminalState(event({ data: { source_id: 'other', state: 'ready' } }), 'abc')
    ).toBeNull()
    expect(
      mediaTerminalState(event({ kind: 'media_progress', data: { source_id: 'abc' } }), 'abc')
    ).toBeNull()
  })
})
