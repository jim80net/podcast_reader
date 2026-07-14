import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { classifyServeStatus } from './serve-status'

function fixture(name: string): string {
  return readFileSync(new URL(`./fixtures/tailscale-serve/${name}.json`, import.meta.url), 'utf8')
}

describe('classifyServeStatus', () => {
  it('classifies an absent HTTPS 443 listener as empty', () => {
    expect(classifyServeStatus(fixture('empty'))).toEqual({ kind: 'empty' })
  })

  it('extracts the exact root proxy and private URL from a known status shape', () => {
    expect(classifyServeStatus(fixture('owned'))).toEqual({
      kind: 'mapping',
      target: 'http://127.0.0.1:43127',
      url: 'https://desktop.example.ts.net'
    })
  })

  it('treats an unrelated path mapping as occupied, even when its target matches', () => {
    expect(classifyServeStatus(fixture('unrelated-path'))).toEqual({
      kind: 'conflict',
      reason: 'HTTPS 443 has a non-root or ambiguous web handler'
    })
  })

  it.each([
    ['malformed JSON', '{'],
    ['unexpected root type', '[]'],
    ['unexpected nested shape', fixture('unexpected-shape')],
    ['empty TCP listener', '{"TCP":{"443":{}},"Web":{}}'],
    ['non-port TCP listener', '{"TCP":{"future":{"HTTPS":true}},"Web":{}}'],
    [
      'public hostname',
      '{"TCP":{"443":{"HTTPS":true}},"Web":{"evil.example:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:1"}}}}}'
    ],
    ['unknown root field', '{"FutureConfig":{}}'],
    ['funnel enabled', '{"AllowFunnel":{"443":true}}']
  ])('fails closed on %s', (_label, text) => {
    expect(classifyServeStatus(text)).toMatchObject({ kind: 'conflict' })
  })

  it('ignores a valid mapping on another listener while 443 is empty', () => {
    const status = JSON.stringify({
      TCP: { '8443': { HTTPS: true } },
      Web: {
        'desktop.example.ts.net:8443': {
          Handlers: { '/': { Proxy: 'http://127.0.0.1:9000' } }
        }
      },
      AllowFunnel: {}
    })
    expect(classifyServeStatus(status)).toEqual({ kind: 'empty' })
  })

  it('fails closed when TCP and Web disagree about listener 443', () => {
    const missingWeb = JSON.stringify({ TCP: { '443': { HTTPS: true } }, Web: {} })
    expect(classifyServeStatus(missingWeb)).toEqual({
      kind: 'conflict',
      reason: 'HTTPS 443 status is internally inconsistent'
    })
  })

  it('rejects a 443 listener that is not exactly HTTPS-only', () => {
    const ambiguous = JSON.stringify({
      TCP: { '443': { HTTPS: true, HTTP: true } },
      Web: {
        'desktop.example.ts.net:443': {
          Handlers: { '/': { Proxy: 'http://127.0.0.1:43127' } }
        }
      }
    })
    expect(classifyServeStatus(ambiguous)).toMatchObject({ kind: 'conflict' })
  })
})
