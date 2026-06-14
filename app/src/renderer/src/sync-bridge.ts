import type { MediaPlayer } from './media-player'

/**
 * Parent side of the `pr-sync` postMessage protocol (media-playback spec, task
 * 7.2) — the seam between the floating media player and the opaque-origin
 * transcript iframe.
 *
 * The artifact iframe is sandboxed `allow-scripts` with NO `allow-same-origin`,
 * so its origin is opaque and `event.origin` is unusable. Messages are
 * therefore validated by BOTH the channel tag (`pr-sync`) AND
 * `event.source === frame.contentWindow`. The dual filter is mandatory: the
 * YouTube control iframe ALSO posts messages to the renderer window, and
 * without the source check its `infoDelivery`/`onReady` frames could be
 * mistaken for sync messages.
 *
 * Sync protocol (design "Sync protocol" table):
 *   parent → iframe : {ch:'pr-sync', type:'time', t}  (throttled ~4 Hz)
 *   iframe → parent : {ch:'pr-sync', type:'seek', t}  → player.seekTo(t)
 *   iframe → parent : {ch:'pr-sync', type:'ready'}    → handshake
 */

export const SYNC_CHANNEL = 'pr-sync'

/** ~4 Hz time forwarding (250 ms) per the design's throttle. */
const DEFAULT_THROTTLE_MS = 250

export type SyncMessage = { type: 'seek'; t: number } | { type: 'ready' }

/**
 * Validate a raw `message` payload against the `pr-sync` protocol. Returns the
 * parsed message, or null for anything that is not a well-formed sync frame
 * (foreign channels, YouTube control messages, malformed payloads). Source
 * identity is checked separately by the bridge.
 */
export function parseSyncMessage(data: unknown): SyncMessage | null {
  if (typeof data !== 'object' || data === null) return null
  const d = data as Record<string, unknown>
  if (d['ch'] !== SYNC_CHANNEL) return null
  if (d['type'] === 'seek' && typeof d['t'] === 'number') return { type: 'seek', t: d['t'] }
  if (d['type'] === 'ready') return { type: 'ready' }
  return null
}

export interface SyncBridgeOptions {
  /** The renderer window that receives `message` events (defaults to global). */
  win?: Window
  /** The kind-agnostic player to drive on seek and read time from. */
  player: MediaPlayer
  /** The transcript iframe's contentWindow — the only honored message source. */
  frameWindow: Window
  /** Called on the iframe's `{type:'ready'}` handshake. */
  onReady?: () => void
  /** Time source (test seam). */
  now?: () => number
  /** Time-forwarding throttle in ms. */
  throttleMs?: number
}

export interface SyncBridge {
  destroy(): void
}

/**
 * Wire the player ↔ transcript-iframe sync. Returns a handle whose `destroy`
 * detaches the listener — the Reader calls it in ViewCleanup.
 */
export function createSyncBridge(opts: SyncBridgeOptions): SyncBridge {
  const win = opts.win ?? window
  const now = opts.now ?? (() => Date.now())
  const throttleMs = opts.throttleMs ?? DEFAULT_THROTTLE_MS

  const onMessage = (event: MessageEvent): void => {
    // Dual filter: the message must come from the transcript frame AND carry
    // the pr-sync channel tag. Either failing → drop (YouTube control messages
    // and any foreign window are rejected here).
    if (event.source !== opts.frameWindow) return
    const message = parseSyncMessage(event.data)
    if (message === null) return
    if (message.type === 'seek') opts.player.seekTo(message.t)
    else opts.onReady?.()
  }
  win.addEventListener('message', onMessage)

  // Forward player time → iframe as throttled {type:'time'} frames.
  let lastSent = Number.NEGATIVE_INFINITY
  opts.player.onTime((t) => {
    const ts = now()
    if (ts - lastSent < throttleMs) return
    lastSent = ts
    opts.frameWindow.postMessage({ ch: SYNC_CHANNEL, type: 'time', t }, '*')
  })

  return {
    destroy: () => win.removeEventListener('message', onMessage)
  }
}
