import { describe, expect, it } from 'vitest'

import { isMediaReady, mediaTerminalState } from './media-events'
import type { MediaStateEvent, PipelineEvent } from '../../shared/types'

const mediaState = (
  sourceId: string,
  state: MediaStateEvent['data']['state']
): MediaStateEvent => ({
  kind: 'media_state',
  step: null,
  message: '',
  data: { source_id: sourceId, state }
})

describe('isMediaReady', () => {
  it('matches a media_state ready event for the same source_id', () => {
    expect(isMediaReady(mediaState('abc', 'ready'), 'abc')).toBe(true)
  })

  it('ignores other source ids, other states, and other kinds', () => {
    expect(isMediaReady(mediaState('other', 'ready'), 'abc')).toBe(false)
    expect(isMediaReady(mediaState('abc', 'preparing'), 'abc')).toBe(false)
    expect(
      isMediaReady(
        {
          kind: 'pack_state',
          step: null,
          message: '',
          data: { pack_id: 'pack-1', state: 'installed' }
        },
        'abc'
      )
    ).toBe(false)
  })
})

describe('mediaTerminalState', () => {
  it('returns the terminal state for the matching source', () => {
    expect(mediaTerminalState(mediaState('abc', 'ready'), 'abc')).toBe('ready')
    expect(mediaTerminalState(mediaState('abc', 'unavailable'), 'abc')).toBe('unavailable')
  })

  it('returns null for non-terminal states, other sources, and other kinds', () => {
    expect(
      mediaTerminalState(mediaState('abc', 'preparing'), 'abc')
    ).toBeNull()
    expect(
      mediaTerminalState(mediaState('other', 'ready'), 'abc')
    ).toBeNull()
    expect(
      mediaTerminalState(
        {
          kind: 'media_progress',
          step: null,
          message: '',
          data: { source_id: 'abc' }
        } satisfies PipelineEvent,
        'abc'
      )
    ).toBeNull()
  })
})
