import { describe, expect, it } from 'vitest'

import {
  EMBED_COMMAND_SOURCE,
  EMBED_EVENT_SOURCE,
  buildSeekCommand,
  parseEmbedEvent
} from './embed-protocol'

describe('embed protocol source tags', () => {
  it('match the Python embed page literals (cross-language contract)', () => {
    expect(EMBED_EVENT_SOURCE).toBe('pr-embed')
    expect(EMBED_COMMAND_SOURCE).toBe('pr-embed-cmd')
  })
})

describe('buildSeekCommand', () => {
  it('tags the command source so the page accepts it', () => {
    expect(buildSeekCommand(42.5)).toEqual({
      source: 'pr-embed-cmd',
      type: 'seek',
      seconds: 42.5
    })
  })
})

describe('parseEmbedEvent', () => {
  it('parses ready / time / error events from the page', () => {
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'ready' })).toEqual({ type: 'ready' })
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'time', seconds: 12.5 })).toEqual({
      type: 'time',
      seconds: 12.5
    })
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'error', code: 150 })).toEqual({
      type: 'error',
      code: 150
    })
  })

  it('ignores foreign messages and malformed payloads', () => {
    expect(parseEmbedEvent({ source: 'other', type: 'ready' })).toBeNull()
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'time' })).toBeNull() // no seconds
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'time', seconds: 'x' })).toBeNull()
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'bogus' })).toBeNull()
    expect(parseEmbedEvent('garbage')).toBeNull()
    expect(parseEmbedEvent(null)).toBeNull()
  })

  it('defaults a missing error code to 0 rather than dropping the error', () => {
    expect(parseEmbedEvent({ source: 'pr-embed', type: 'error' })).toEqual({
      type: 'error',
      code: 0
    })
  })
})
