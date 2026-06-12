import { describe, expect, it } from 'vitest'

import { broadcastTo } from './broadcast'
import type { BroadcastWindowLike } from './broadcast'

function makeWindow(opts: { windowDestroyed?: boolean; contentsDestroyed?: boolean } = {}): {
  window: BroadcastWindowLike
  received: { channel: string; payload: unknown }[]
} {
  const received: { channel: string; payload: unknown }[] = []
  const window: BroadcastWindowLike = {
    isDestroyed: () => opts.windowDestroyed ?? false,
    webContents: {
      isDestroyed: () => opts.contentsDestroyed ?? false,
      send: (channel: string, payload: unknown) => {
        if (opts.windowDestroyed || opts.contentsDestroyed) {
          throw new TypeError('Object has been destroyed')
        }
        received.push({ channel, payload })
      }
    }
  }
  return { window, received }
}

describe('broadcastTo', () => {
  it('sends to every live window', () => {
    const a = makeWindow()
    const b = makeWindow()
    broadcastTo([a.window, b.window], 'engine:status', { state: 'ready' })
    expect(a.received).toEqual([{ channel: 'engine:status', payload: { state: 'ready' } }])
    expect(b.received).toEqual([{ channel: 'engine:status', payload: { state: 'ready' } }])
  })

  it('skips destroyed windows without throwing, still reaching live ones', () => {
    const dead = makeWindow({ windowDestroyed: true })
    const live = makeWindow()
    expect(() => broadcastTo([dead.window, live.window], 'engine:event', { kind: 'log' })).not.toThrow()
    expect(dead.received).toEqual([])
    expect(live.received).toHaveLength(1)
  })

  it('skips windows whose webContents are destroyed (teardown race)', () => {
    const dying = makeWindow({ contentsDestroyed: true })
    const live = makeWindow()
    expect(() => broadcastTo([dying.window, live.window], 'jobs:hydrated', [])).not.toThrow()
    expect(dying.received).toEqual([])
    expect(live.received).toHaveLength(1)
  })
})
