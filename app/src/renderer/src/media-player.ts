import { el } from './dom'
import { buildSeekCommand, parseEmbedEvent } from './embed-protocol'
import type { MediaInfo } from '../../shared/types'

/**
 * The inline media player (media-playback spec, revised): a docked panel at the
 * top of the Reader — NOT a floating overlay — so it never covers the
 * transcript. The transcript iframe sits below it and reflows; the panel can be
 * minimized to its header strip to reclaim vertical space. It renders by
 * `MediaInfo.kind` behind ONE uniform `{seekTo, onTime, destroy}` interface so
 * the sync bridge is kind-agnostic:
 *   - video → <video controls src="app://media/ID">
 *   - audio → compact <audio controls src="app://media/ID">
 *   - youtube → an <iframe> loading the engine-hosted embed page
 *     (`http://127.0.0.1:<port>/v1/embed/<id>`), driven by the postMessage
 *     protocol in embed-protocol.ts. The loopback http origin is what makes
 *     YouTube accept the embed (the file:// renderer triggers Error 152/153);
 *     on any embed error we fall back to a "Watch on YouTube" link (opens the
 *     OS browser via the main process's window-open handler).
 *
 * Media bytes for video/audio load directly via the main-mediated app:// scheme
 * (media-protocol.ts); the renderer never holds the engine token.
 */

// ---- uniform interface ------------------------------------------------------

export interface MediaPlayer {
  /** The mounted panel root (the Reader appends/removes it). */
  readonly el: HTMLElement
  /** Seek playback to `t` seconds. */
  seekTo(t: number): void
  /** Subscribe to playback-time updates (seconds). */
  onTime(cb: (t: number) => void): void
  /** Tear down listeners/timers and detach the panel. */
  destroy(): void
}

/**
 * Sandbox for the YouTube embed iframe. The engine page is loaded cross-origin
 * from the loopback http origin, so:
 *   - allow-scripts: run the YouTube IFrame API + our controller,
 *   - allow-same-origin: read `location.origin` (the 152/153 fix) and run the
 *     player; safe because the iframe's origin (127.0.0.1) differs from the
 *     renderer's file:// origin, so it cannot script into the app,
 *   - allow-popups + allow-popups-to-escape-sandbox: a "Watch on YouTube" link
 *     inside the player still opens (routed to the OS browser),
 *   - allow-presentation: fullscreen.
 * Locked by a unit test against drift.
 */
export const YOUTUBE_IFRAME_SANDBOX =
  'allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-presentation'

/** No-`ready` grace period before we give up on the embed and show the link. */
const EMBED_READY_TIMEOUT_MS = 10_000

export interface CreatePlayerOptions {
  win?: Window
  /** Test seam: resolve the engine embed URL for a YouTube id. */
  getEmbedUrl?: (videoId: string) => Promise<string | null>
  /**
   * Hide the player. The Reader owns the media column, so it supplies this to
   * collapse the whole column (reclaiming the width for the transcript) and to
   * reveal a "Show video" control; the header's hide button just invokes it.
   */
  onHide?: () => void
}

/**
 * Build the inline player for a `ready` source. `sourceId` keys the privileged
 * `app://media/<sourceId>` URL for video/audio (ignored for YouTube, which
 * embeds by `youtube_id`). The Reader gates `preparing` sources and only builds
 * the player once the media is ready.
 */
export function createMediaPlayer(
  sourceId: string,
  info: MediaInfo,
  opts: CreatePlayerOptions = {}
): MediaPlayer {
  const win = opts.win ?? window
  const getEmbedUrl = opts.getEmbedUrl ?? ((id: string) => window.api.youtubeEmbedUrl(id))
  const timeListeners: ((t: number) => void)[] = []
  const emitTime = (t: number): void => {
    for (const cb of timeListeners) cb(t)
  }

  const title = el('span', { class: 'media-title', text: mediaTitle(info.kind) })
  const hideBtn = el('button', {
    class: 'media-hide',
    text: '✕',
    attrs: { type: 'button', title: 'Hide player', 'aria-label': 'Hide player' }
  })
  const header = el('div', { class: 'media-header' }, title, hideBtn)
  const body = el('div', { class: 'media-body' })
  const panel = el(
    'div',
    {
      class: 'media-player',
      attrs: { 'data-kind': info.kind, role: 'region', 'aria-label': 'Media player' }
    },
    header,
    body
  )

  const surface = buildSurface(sourceId, info, body, emitTime, win, getEmbedUrl)

  hideBtn.addEventListener('click', () => opts.onHide?.())

  return {
    el: panel,
    // Guard the seek input once for every surface: a non-finite t (NaN/Infinity
    // from a malformed passage timestamp) must never reach a media element's
    // currentTime or the YouTube seek command.
    seekTo: (t) => {
      if (Number.isFinite(t)) surface.seekTo(Math.max(0, t))
    },
    onTime: (cb) => timeListeners.push(cb),
    // NOTE: destroy() tears down listeners/timers but does NOT remove `el` from
    // the DOM — the Reader owns that node (it replaceChildren()s the slot on
    // remount and the view container on teardown).
    destroy: () => surface.destroy()
  }
}

// ---- per-kind surfaces ------------------------------------------------------

interface Surface {
  seekTo(t: number): void
  destroy(): void
}

function buildSurface(
  sourceId: string,
  info: MediaInfo,
  body: HTMLElement,
  emitTime: (t: number) => void,
  win: Window,
  getEmbedUrl: (videoId: string) => Promise<string | null>
): Surface {
  if (info.kind === 'youtube') {
    return buildYoutubeSurface(info.youtube_id, body, emitTime, win, getEmbedUrl)
  }
  if (info.kind === 'audio') return buildMediaElementSurface('audio', sourceId, body, emitTime)
  return buildMediaElementSurface('video', sourceId, body, emitTime)
}

function buildMediaElementSurface(
  tag: 'video' | 'audio',
  sourceId: string,
  body: HTMLElement,
  emitTime: (t: number) => void
): Surface {
  const element = el(tag, {
    class: `media-${tag}`,
    attrs: { controls: '', src: `app://media/${sourceId}`, preload: 'metadata' }
  })
  const onTimeUpdate = (): void => emitTime(element.currentTime)
  element.addEventListener('timeupdate', onTimeUpdate)
  body.append(element)
  return {
    seekTo: (t) => {
      element.currentTime = t
      void element.play().catch(() => {
        /* autoplay may be blocked; the user can press play */
      })
    },
    destroy: () => element.removeEventListener('timeupdate', onTimeUpdate)
  }
}

function buildYoutubeSurface(
  youtubeId: string,
  body: HTMLElement,
  emitTime: (t: number) => void,
  win: Window,
  getEmbedUrl: (videoId: string) => Promise<string | null>
): Surface {
  const iframe = el('iframe', {
    class: 'media-youtube',
    attrs: {
      title: 'YouTube player',
      allow: 'autoplay; encrypted-media; picture-in-picture; fullscreen',
      sandbox: YOUTUBE_IFRAME_SANDBOX
    }
  })
  const fallback = el('a', {
    class: 'media-youtube-fallback',
    text: 'Watch on YouTube',
    attrs: {
      href: `https://www.youtube.com/watch?v=${encodeURIComponent(youtubeId)}`,
      target: '_blank',
      rel: 'noreferrer'
    }
  })
  fallback.hidden = true
  body.append(iframe, fallback)

  let ready = false
  let destroyed = false
  // No `ready` within the grace period (or any error) → the embed isn't going
  // to work; surface the "Watch on YouTube" link instead of a black box.
  const timeout = win.setTimeout(showFallback, EMBED_READY_TIMEOUT_MS)
  function showFallback(): void {
    if (destroyed) return
    win.clearTimeout(timeout)
    iframe.hidden = true
    fallback.hidden = false
  }

  const onMessage = (event: MessageEvent): void => {
    // Identity: the message must come from OUR embed iframe's window. The
    // engine page is same-(loopback)-origin, so we also require a loopback
    // origin as defense in depth (rejects any other frame that somehow forged
    // the source tag).
    if (event.source !== iframe.contentWindow) return
    if (!isLoopbackOrigin(event.origin)) return
    const ev = parseEmbedEvent(event.data)
    if (ev === null) return
    if (ev.type === 'ready') {
      ready = true
      win.clearTimeout(timeout)
    } else if (ev.type === 'time') {
      emitTime(ev.seconds)
    } else if (ev.type === 'error') {
      showFallback()
    }
  }
  win.addEventListener('message', onMessage)
  // Belt-and-suspenders: the native iframe `error` event is unreliable for
  // cross-origin loads, so the real failure path is the embed page's `error`
  // postMessage (handled above) plus the no-`ready` timeout; this just covers
  // an outright load failure of the engine page itself.
  iframe.addEventListener('error', showFallback)

  // Resolve the loopback embed URL (engine may not be ready → fallback).
  void getEmbedUrl(youtubeId)
    .then((url) => {
      if (destroyed) return
      if (url === null) showFallback()
      else iframe.src = url
    })
    .catch(() => showFallback())

  return {
    seekTo: (t) => {
      if (ready) iframe.contentWindow?.postMessage(buildSeekCommand(t), '*')
    },
    destroy: () => {
      destroyed = true
      win.clearTimeout(timeout)
      win.removeEventListener('message', onMessage)
    }
  }
}

// ---- helpers ----------------------------------------------------------------

/**
 * The engine embed page is served from the loopback http origin. Parse and
 * match the hostname EXACTLY — a `startsWith` check would also accept
 * `http://127.0.0.1.evil.com` (cubic). Exported for the unit test that pins
 * this bypass-resistance.
 */
export function isLoopbackOrigin(origin: string): boolean {
  let url: URL
  try {
    url = new URL(origin)
  } catch {
    return false
  }
  return url.protocol === 'http:' && (url.hostname === '127.0.0.1' || url.hostname === 'localhost')
}

function mediaTitle(kind: MediaInfo['kind']): string {
  switch (kind) {
    case 'youtube':
      return 'YouTube'
    case 'audio':
      return 'Audio'
    case 'video':
      return 'Video'
    case 'unavailable':
      return 'Media'
  }
}
