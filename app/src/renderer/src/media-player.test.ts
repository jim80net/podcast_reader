import { describe, expect, it } from 'vitest'

import {
  GEOMETRY_KEY,
  YOUTUBE_IFRAME_SANDBOX,
  clampGeometry,
  loadGeometry,
  saveGeometry,
  youtubeCommand,
  youtubeEmbedUrl,
  youtubeListening,
  youtubeTimeFromMessage
} from './media-player'

/**
 * Pure player logic (tasks 7.1, 7.3). The DOM panel (drag/resize/render) is
 * thin and covered by Playwright e2e; here we unit-test the YouTube raw-iframe
 * postMessage wiring and the geometry persistence (real YouTube cannot load in
 * CI — design "YouTube path" testing note).
 */

describe('youtubeEmbedUrl', () => {
  it('builds a youtube-nocookie embed with the JS-API flag enabled', () => {
    const url = youtubeEmbedUrl('dQw4w9WgXcQ')
    expect(url).toBe('https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?enablejsapi=1')
  })

  it('omits the origin param so inbound infoDelivery is not origin-pinned away', () => {
    // origin=null (or any bogus origin) makes the player target outbound
    // infoDelivery at that origin and our time-sync handler never receives it.
    expect(youtubeEmbedUrl('dQw4w9WgXcQ')).not.toContain('origin=')
  })

  it('encodes the video id (defense-in-depth, ids are alnum/-/_ in practice)', () => {
    expect(youtubeEmbedUrl('a b')).toContain('/embed/a%20b?')
  })
})

describe('YOUTUBE_IFRAME_SANDBOX', () => {
  it('grants the cross-origin embed scripts + its own origin, locked against drift', () => {
    // allow-same-origin is intentional and safe: the embed is cross-origin
    // youtube-nocookie, so this grants it YouTube's origin (needed for the
    // player), not the renderer's app:// origin. No JS IFrame API is loaded.
    expect(YOUTUBE_IFRAME_SANDBOX).toBe('allow-scripts allow-same-origin allow-presentation')
  })
})

describe('youtube raw postMessage control protocol', () => {
  it('builds the listening handshake frame', () => {
    expect(youtubeListening()).toEqual({ event: 'listening', id: 1, channel: 'widget' })
  })

  it('builds a seekTo command (NOT the JS IFrame API)', () => {
    expect(youtubeCommand('seekTo', [30, true])).toEqual({
      event: 'command',
      func: 'seekTo',
      args: [30, true],
      id: 1,
      channel: 'widget'
    })
  })

  it('reads currentTime from an infoDelivery message', () => {
    expect(
      youtubeTimeFromMessage({ event: 'infoDelivery', info: { currentTime: 12.75 } })
    ).toBe(12.75)
  })

  it('returns null for non-infoDelivery or missing currentTime', () => {
    expect(youtubeTimeFromMessage({ event: 'onReady' })).toBeNull()
    expect(youtubeTimeFromMessage({ event: 'infoDelivery', info: {} })).toBeNull()
    expect(youtubeTimeFromMessage('garbage')).toBeNull()
    expect(youtubeTimeFromMessage(null)).toBeNull()
  })
})

// ---- geometry persistence (injectable storage) ------------------------------

function memoryStorage(): Storage {
  const map = new Map<string, string>()
  return {
    get length() {
      return map.size
    },
    clear: () => map.clear(),
    getItem: (k) => map.get(k) ?? null,
    key: (i) => [...map.keys()][i] ?? null,
    removeItem: (k) => map.delete(k),
    setItem: (k, v) => {
      map.set(k, v)
    }
  }
}

describe('geometry persistence', () => {
  it('round-trips geometry through storage under the documented key', () => {
    const store = memoryStorage()
    saveGeometry({ x: 10, y: 20, w: 480, h: 270 }, store)
    expect(store.getItem(GEOMETRY_KEY)).not.toBeNull()
    expect(loadGeometry(store)).toEqual({ x: 10, y: 20, w: 480, h: 270 })
  })

  it('returns null when nothing is stored or the value is corrupt', () => {
    const store = memoryStorage()
    expect(loadGeometry(store)).toBeNull()
    store.setItem(GEOMETRY_KEY, 'not json')
    expect(loadGeometry(store)).toBeNull()
    store.setItem(GEOMETRY_KEY, JSON.stringify({ x: 1 })) // missing fields
    expect(loadGeometry(store)).toBeNull()
  })

  it('clamps geometry into the viewport, enforcing a minimum size', () => {
    // Off-screen / oversized → pulled back inside an 800x600 viewport.
    expect(clampGeometry({ x: -50, y: -50, w: 2000, h: 2000 }, 800, 600)).toEqual({
      x: 0,
      y: 0,
      w: 800,
      h: 600
    })
    // A tiny size is bumped to the minimum.
    const c = clampGeometry({ x: 10, y: 10, w: 10, h: 10 }, 800, 600)
    expect(c.w).toBeGreaterThanOrEqual(160)
    expect(c.h).toBeGreaterThanOrEqual(90)
  })
})
