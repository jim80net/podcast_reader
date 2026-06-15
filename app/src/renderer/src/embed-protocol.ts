/**
 * postMessage protocol between the Reader's YouTube iframe and the engine-hosted
 * embed page (`src/podcast_reader/engine/embed.py`). The page is served from the
 * loopback http origin so YouTube accepts it (the file:// renderer triggers
 * Error 152/153); we talk to it only via these messages — no cross-origin DOM
 * access.
 *
 * The source tags MUST match the Python page's literals; `embed-protocol.test.ts`
 * and `tests/engine/test_embed.py` each pin them so the two sides can't drift.
 */

/** Events the embed page posts to us (page → app). */
export const EMBED_EVENT_SOURCE = 'pr-embed'
/** Commands we post to the embed page (app → page). */
export const EMBED_COMMAND_SOURCE = 'pr-embed-cmd'

export type EmbedEvent =
  | { type: 'ready' }
  | { type: 'time'; seconds: number }
  | { type: 'error'; code: number }

/** Build the seek command to post into the embed iframe's contentWindow. */
export function buildSeekCommand(seconds: number): {
  source: string
  type: 'seek'
  seconds: number
} {
  return { source: EMBED_COMMAND_SOURCE, type: 'seek', seconds }
}

/**
 * Parse a `message` payload from the embed page into a typed event, or null if
 * it isn't one of ours (wrong source / shape). Tolerates the `data` arriving as
 * an object (postMessage preserves structure within the app).
 */
export function parseEmbedEvent(data: unknown): EmbedEvent | null {
  if (typeof data !== 'object' || data === null) return null
  const d = data as Record<string, unknown>
  if (d['source'] !== EMBED_EVENT_SOURCE) return null
  switch (d['type']) {
    case 'ready':
      return { type: 'ready' }
    case 'time':
      return typeof d['seconds'] === 'number' && Number.isFinite(d['seconds'])
        ? { type: 'time', seconds: d['seconds'] }
        : null
    case 'error':
      return { type: 'error', code: typeof d['code'] === 'number' ? d['code'] : 0 }
    default:
      return null
  }
}
