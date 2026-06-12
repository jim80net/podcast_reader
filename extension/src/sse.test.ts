import { describe, expect, it } from 'vitest'

import { SseParser } from './sse'

describe('SseParser', () => {
  it('parses data frames split across chunks', () => {
    const parser = new SseParser()
    expect(parser.push('data: {"a"')).toEqual([])
    expect(parser.push(':1}\n\n')).toEqual(['{"a":1}'])
  })

  it('ignores comment heartbeats and joins multi-line data', () => {
    const parser = new SseParser()
    expect(parser.push(': keepalive\n\ndata: one\ndata: two\n\n')).toEqual(['one\ntwo'])
  })

  it('handles CRLF line endings', () => {
    const parser = new SseParser()
    expect(parser.push('data: x\r\n\r\n')).toEqual(['x'])
  })
})
