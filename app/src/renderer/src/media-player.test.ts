import { describe, expect, it } from 'vitest'

import { YOUTUBE_IFRAME_SANDBOX } from './media-player'

/**
 * The player's DOM (inline panel render, per-kind surfaces, YouTube embed +
 * fallback) is covered by the Playwright e2e suite. Here we only pin the
 * security-relevant iframe sandbox against drift; the embed postMessage
 * protocol is unit-tested in embed-protocol.test.ts.
 */
describe('YOUTUBE_IFRAME_SANDBOX', () => {
  it('grants scripts + own (loopback) origin and lets a "Watch on YouTube" link escape', () => {
    // allow-same-origin is safe here: the engine embed page loads from the
    // loopback http origin, distinct from the renderer's file:// origin, so the
    // iframe can read location.origin (the 152/153 fix) without scripting into
    // the app. allow-popups* lets YouTube's own link reach the OS browser.
    expect(YOUTUBE_IFRAME_SANDBOX).toBe(
      'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-presentation'
    )
  })
})
