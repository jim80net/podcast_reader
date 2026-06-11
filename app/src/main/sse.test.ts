import { describe, expect, it } from 'vitest'

import { SseParser } from './sse'

describe('SseParser', () => {
  it('emits data payloads for complete events', () => {
    const parser = new SseParser()
    expect(parser.push('data: {"kind":"warning"}\n\n')).toEqual(['{"kind":"warning"}'])
  })

  it('buffers partial events across chunks', () => {
    const parser = new SseParser()
    expect(parser.push('data: {"a"')).toEqual([])
    expect(parser.push(':1}\n')).toEqual([])
    expect(parser.push('\n')).toEqual(['{"a":1}'])
  })

  it('ignores comment heartbeats (": keepalive")', () => {
    const parser = new SseParser()
    expect(parser.push(': keepalive\n\n')).toEqual([])
    expect(parser.push(': keepalive\n\ndata: x\n\n')).toEqual(['x'])
  })

  it('joins multi-line data fields with newlines', () => {
    const parser = new SseParser()
    expect(parser.push('data: line1\ndata: line2\n\n')).toEqual(['line1\nline2'])
  })

  it('handles CRLF line endings', () => {
    const parser = new SseParser()
    expect(parser.push('data: x\r\n\r\n')).toEqual(['x'])
  })

  it('handles multiple events in one chunk', () => {
    const parser = new SseParser()
    expect(parser.push('data: a\n\ndata: b\n\n')).toEqual(['a', 'b'])
  })

  it('tolerates "data:" with no space', () => {
    const parser = new SseParser()
    expect(parser.push('data:x\n\n')).toEqual(['x'])
  })
})
