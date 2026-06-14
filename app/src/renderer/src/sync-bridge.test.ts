import { describe, expect, it, vi } from 'vitest'

import { SYNC_CHANNEL, createSyncBridge, parseSyncMessage } from './sync-bridge'
import type { MediaPlayer } from './media-player'

/**
 * Parent side of the `pr-sync` postMessage protocol (media-playback spec, task
 * 7.2). The transcript iframe is opaque-origin, so messages are validated by
 * BOTH the channel tag and `event.source === frame.contentWindow` — the
 * YouTube control iframe also posts to the renderer window and must be dropped.
 */

describe('parseSyncMessage', () => {
  it('accepts a seek with a numeric t', () => {
    expect(parseSyncMessage({ ch: SYNC_CHANNEL, type: 'seek', t: 12.5 })).toEqual({
      type: 'seek',
      t: 12.5
    })
  })

  it('accepts the ready handshake', () => {
    expect(parseSyncMessage({ ch: SYNC_CHANNEL, type: 'ready' })).toEqual({ type: 'ready' })
  })

  it('drops messages without the pr-sync channel tag (e.g. YouTube control)', () => {
    expect(parseSyncMessage({ event: 'infoDelivery', info: { currentTime: 3 } })).toBeNull()
    expect(parseSyncMessage({ ch: 'other', type: 'seek', t: 1 })).toBeNull()
  })

  it('drops malformed sync payloads', () => {
    expect(parseSyncMessage(null)).toBeNull()
    expect(parseSyncMessage('string')).toBeNull()
    expect(parseSyncMessage({ ch: SYNC_CHANNEL })).toBeNull()
    expect(parseSyncMessage({ ch: SYNC_CHANNEL, type: 'seek' })).toBeNull() // no t
    expect(parseSyncMessage({ ch: SYNC_CHANNEL, type: 'seek', t: 'x' })).toBeNull()
    expect(parseSyncMessage({ ch: SYNC_CHANNEL, type: 'nope' })).toBeNull()
  })
})

// ---- bridge wiring (DOM-shaped fakes; vitest runs in node) -------------------

interface FakeWindow {
  listeners: ((e: MessageEvent) => void)[]
  addEventListener(type: 'message', cb: (e: MessageEvent) => void): void
  removeEventListener(type: 'message', cb: (e: MessageEvent) => void): void
  dispatch(data: unknown, source: unknown): void
}

function fakeWindow(): FakeWindow {
  const listeners: ((e: MessageEvent) => void)[] = []
  return {
    listeners,
    addEventListener: (_t, cb) => listeners.push(cb),
    removeEventListener: (_t, cb) => {
      const i = listeners.indexOf(cb)
      if (i >= 0) listeners.splice(i, 1)
    },
    dispatch: (data, source) => {
      for (const cb of [...listeners]) cb({ data, source } as MessageEvent)
    }
  }
}

function fakePlayer(): MediaPlayer & { seeks: number[]; timeCb: ((t: number) => void) | null } {
  const seeks: number[] = []
  let timeCb: ((t: number) => void) | null = null
  return {
    seeks,
    get timeCb() {
      return timeCb
    },
    el: {} as HTMLElement,
    seekTo: (t: number) => seeks.push(t),
    onTime: (cb: (t: number) => void) => {
      timeCb = cb
    },
    destroy: () => {}
  }
}

describe('createSyncBridge', () => {
  it('seeks the player only for messages from the transcript frame window', () => {
    const win = fakeWindow()
    const player = fakePlayer()
    const frameWindow = { id: 'frame' }
    createSyncBridge({
      win: win as unknown as Window,
      player,
      frameWindow: frameWindow as unknown as Window
    })

    // From the transcript frame: honored.
    win.dispatch({ ch: SYNC_CHANNEL, type: 'seek', t: 42 }, frameWindow)
    expect(player.seeks).toEqual([42])

    // Same payload from a DIFFERENT source (e.g. the YouTube iframe): dropped.
    win.dispatch({ ch: SYNC_CHANNEL, type: 'seek', t: 99 }, { id: 'youtube' })
    expect(player.seeks).toEqual([42])
  })

  it('forwards player time to the transcript frame as throttled pr-sync time', () => {
    const win = fakeWindow()
    const player = fakePlayer()
    const posted: { data: unknown }[] = []
    const frameWindow = {
      postMessage: (data: unknown) => posted.push({ data })
    }
    let clock = 0
    createSyncBridge({
      win: win as unknown as Window,
      player,
      frameWindow: frameWindow as unknown as Window,
      now: () => clock,
      throttleMs: 250
    })

    player.timeCb?.(1) // first emit always passes
    clock = 100
    player.timeCb?.(1.1) // within the window → throttled
    clock = 300
    player.timeCb?.(1.3) // window elapsed → passes

    expect(posted.map((p) => (p.data as { type: string; t: number }).t)).toEqual([1, 1.3])
    expect((posted[0]?.data as { ch: string }).ch).toBe(SYNC_CHANNEL)
    expect((posted[0]?.data as { type: string }).type).toBe('time')
  })

  it('detaches the message listener on destroy', () => {
    const win = fakeWindow()
    const player = fakePlayer()
    const bridge = createSyncBridge({
      win: win as unknown as Window,
      player,
      frameWindow: {} as unknown as Window
    })
    expect(win.listeners).toHaveLength(1)
    bridge.destroy()
    expect(win.listeners).toHaveLength(0)
  })

  it('invokes the ready handshake callback', () => {
    const win = fakeWindow()
    const player = fakePlayer()
    const onReady = vi.fn()
    const frameWindow = { id: 'frame' }
    createSyncBridge({
      win: win as unknown as Window,
      player,
      frameWindow: frameWindow as unknown as Window,
      onReady
    })
    win.dispatch({ ch: SYNC_CHANNEL, type: 'ready' }, frameWindow)
    expect(onReady).toHaveBeenCalledOnce()
  })
})
