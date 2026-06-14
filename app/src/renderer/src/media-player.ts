import { el } from './dom'
import type { MediaInfo } from '../../shared/types'

/**
 * The floating media player (media-playback spec, task 7.1): a draggable,
 * resizable panel layered over the Reader. It renders by `MediaInfo.kind`
 * behind ONE uniform `{seekTo, onTime, destroy}` interface so the sync bridge
 * is kind-agnostic:
 *   - video → <video controls src="app://media/ID">
 *   - audio → compact <audio controls src="app://media/ID">
 *   - youtube → cross-origin youtube-nocookie <iframe>, driven by the RAW
 *     YouTube iframe postMessage control protocol (NOT the JS IFrame API, so no
 *     third-party JS runs in the renderer's main world — design F1).
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

// ---- geometry persistence ---------------------------------------------------

export const GEOMETRY_KEY = 'pr.media-player.geometry'
const MIN_W = 160
const MIN_H = 90
const DEFAULT_GEOMETRY: Geometry = { x: 24, y: 24, w: 480, h: 300 }

export interface Geometry {
  x: number
  y: number
  w: number
  h: number
}

export function saveGeometry(geometry: Geometry, store: Storage = localStorage): void {
  store.setItem(GEOMETRY_KEY, JSON.stringify(geometry))
}

export function loadGeometry(store: Storage = localStorage): Geometry | null {
  const raw = store.getItem(GEOMETRY_KEY)
  if (raw === null) return null
  try {
    const value = JSON.parse(raw) as Record<string, unknown>
    const { x, y, w, h } = value
    // Require finite numbers (cubic P2): NaN/Infinity from corrupted storage
    // would otherwise propagate into the layout and break the panel.
    if ([x, y, w, h].every((n) => typeof n === 'number' && Number.isFinite(n))) {
      return { x: x as number, y: y as number, w: w as number, h: h as number }
    }
    return null
  } catch {
    return null
  }
}

/** Keep the panel inside the viewport, with a minimum size. */
export function clampGeometry(g: Geometry, viewW: number, viewH: number): Geometry {
  const w = Math.max(MIN_W, Math.min(g.w, viewW))
  const h = Math.max(MIN_H, Math.min(g.h, viewH))
  const x = Math.max(0, Math.min(g.x, viewW - w))
  const y = Math.max(0, Math.min(g.y, viewH - h))
  return { x, y, w, h }
}

// ---- YouTube raw iframe postMessage control protocol (design F1) ------------

/** The youtube-nocookie embed URL with the JS-API flag (postMessage) enabled. */
// Sandbox for the cross-origin YouTube embed (see buildYoutubeSurface for the
// allow-same-origin rationale). Exported so a unit test can lock it against
// drift — this attribute is security-relevant.
export const YOUTUBE_IFRAME_SANDBOX = 'allow-scripts allow-same-origin allow-presentation'

export function youtubeEmbedUrl(youtubeId: string): string {
  // Only enablejsapi=1 (per the design). We deliberately omit the `origin`
  // param: the renderer's origin is the custom app:// scheme, and pinning a
  // bogus origin (e.g. "null") makes the player target its outbound
  // infoDelivery messages at that origin, so our inbound time-sync handler —
  // which drives the highlight — would never receive them. Without `origin`
  // the player broadcasts infoDelivery and our `message` listener gets it.
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(youtubeId)}?enablejsapi=1`
}

/** The handshake that subscribes us to the iframe's infoDelivery stream. */
export function youtubeListening(): Record<string, unknown> {
  return { event: 'listening', id: 1, channel: 'widget' }
}

/** A raw control command (e.g. seekTo) — NOT the JS IFrame API. */
export function youtubeCommand(func: string, args: unknown[]): Record<string, unknown> {
  return { event: 'command', func, args, id: 1, channel: 'widget' }
}

/** Extract currentTime from a YouTube infoDelivery message, or null. */
export function youtubeTimeFromMessage(data: unknown): number | null {
  let parsed: unknown = data
  if (typeof data === 'string') {
    try {
      parsed = JSON.parse(data)
    } catch {
      return null
    }
  }
  if (typeof parsed !== 'object' || parsed === null) return null
  const d = parsed as Record<string, unknown>
  if (d['event'] !== 'infoDelivery') return null
  const info = d['info']
  if (typeof info !== 'object' || info === null) return null
  const t = (info as Record<string, unknown>)['currentTime']
  return typeof t === 'number' ? t : null
}

// ---- factory ----------------------------------------------------------------

export interface CreatePlayerOptions {
  store?: Storage
  win?: Window
}

/**
 * Build the floating player for a `ready` source. `sourceId` keys the
 * privileged `app://media/<sourceId>` URL for video/audio (ignored for
 * YouTube, which embeds by `youtube_id`). The Reader gates `preparing` sources
 * and only builds the player once the media is ready, so the media element
 * never points at a half-written cache file.
 */
export function createMediaPlayer(
  sourceId: string,
  info: MediaInfo,
  opts: CreatePlayerOptions = {}
): MediaPlayer {
  const store = opts.store ?? localStorage
  const win = opts.win ?? window
  const timeListeners: ((t: number) => void)[] = []
  const emitTime = (t: number): void => {
    for (const cb of timeListeners) cb(t)
  }

  const title = el('span', { class: 'media-title', text: mediaTitle(info.kind) })
  const minimizeBtn = el('button', {
    class: 'media-minimize',
    text: '–',
    attrs: { type: 'button', title: 'Minimize', 'aria-label': 'Minimize player' }
  })
  const header = el('div', { class: 'media-header' }, title, minimizeBtn)
  const body = el('div', { class: 'media-body' })
  const resizeHandle = el('div', { class: 'media-resize', attrs: { 'aria-hidden': 'true' } })
  const panel = el(
    'div',
    { class: 'media-player', attrs: { 'data-kind': info.kind, role: 'region', 'aria-label': 'Media player' } },
    header,
    body,
    resizeHandle
  )

  const surface = buildSurface(sourceId, info, body, emitTime, win)

  // ---- geometry + drag + resize ----
  const geometry = clampGeometry(
    loadGeometry(store) ?? DEFAULT_GEOMETRY,
    win.innerWidth || DEFAULT_GEOMETRY.w + 48,
    win.innerHeight || DEFAULT_GEOMETRY.h + 48
  )
  applyGeometry(panel, geometry)

  const cleanups: (() => void)[] = []
  cleanups.push(installDrag(header, panel, geometry, store, win))
  cleanups.push(installResize(resizeHandle, panel, geometry, store, win))

  let minimized = false
  minimizeBtn.addEventListener('click', () => {
    minimized = !minimized
    panel.classList.toggle('minimized', minimized)
    minimizeBtn.textContent = minimized ? '+' : '–'
    minimizeBtn.setAttribute('aria-label', minimized ? 'Restore player' : 'Minimize player')
  })

  return {
    el: panel,
    seekTo: (t) => surface.seekTo(t),
    onTime: (cb) => timeListeners.push(cb),
    destroy: () => {
      for (const fn of cleanups) fn()
      surface.destroy()
      panel.remove()
    }
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
  win: Window
): Surface {
  if (info.kind === 'youtube') return buildYoutubeSurface(info.youtube_id, body, emitTime, win)
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
  win: Window
): Surface {
  const iframe = el('iframe', {
    class: 'media-youtube',
    attrs: {
      src: youtubeEmbedUrl(youtubeId),
      title: 'YouTube player',
      allow: 'autoplay; encrypted-media; picture-in-picture',
      // allow-same-origin is required AND safe here: the iframe loads
      // cross-origin youtube-nocookie content, so allow-same-origin grants it
      // *YouTube's* own origin (needed for its player + the postMessage
      // protocol), NOT the renderer's app:// origin. The same-origin policy
      // still walls it off from our DOM/storage/window.api — design F1's
      // "no third-party JS in our context" is satisfied by never loading the
      // YouTube JS API (we use the raw postMessage protocol below). Dropping
      // allow-same-origin gives the embed an opaque origin and breaks the
      // player. The value is locked by a unit test so it can't drift.
      sandbox: YOUTUBE_IFRAME_SANDBOX
    }
  })
  const fallback = el(
    'a',
    {
      class: 'media-youtube-fallback',
      text: 'Watch on YouTube',
      attrs: {
        href: `https://www.youtube.com/watch?v=${encodeURIComponent(youtubeId)}`,
        target: '_blank',
        rel: 'noreferrer'
      }
    }
  )
  fallback.hidden = true
  body.append(iframe, fallback)

  // Surface an embed error as the "Watch on YouTube" fallback.
  iframe.addEventListener('error', () => {
    iframe.hidden = true
    fallback.hidden = false
  })

  // Raw postMessage control: subscribe (listening) once the frame loads, then
  // read time from its infoDelivery messages.
  const post = (payload: Record<string, unknown>): void => {
    iframe.contentWindow?.postMessage(JSON.stringify(payload), '*')
  }
  const onLoad = (): void => post(youtubeListening())
  iframe.addEventListener('load', onLoad)

  const onMessage = (event: MessageEvent): void => {
    if (event.source !== iframe.contentWindow) return
    const t = youtubeTimeFromMessage(event.data)
    if (t !== null) emitTime(t)
  }
  win.addEventListener('message', onMessage)

  return {
    seekTo: (t) => post(youtubeCommand('seekTo', [t, true])),
    destroy: () => {
      iframe.removeEventListener('load', onLoad)
      win.removeEventListener('message', onMessage)
    }
  }
}

// ---- helpers ----------------------------------------------------------------

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

function applyGeometry(panel: HTMLElement, g: Geometry): void {
  panel.style.left = `${g.x}px`
  panel.style.top = `${g.y}px`
  panel.style.width = `${g.w}px`
  panel.style.height = `${g.h}px`
}

function installDrag(
  handle: HTMLElement,
  panel: HTMLElement,
  geometry: Geometry,
  store: Storage,
  win: Window
): () => void {
  const onPointerDown = (e: PointerEvent): void => {
    if (e.target instanceof HTMLButtonElement) return // minimize button
    e.preventDefault()
    const startX = e.clientX
    const startY = e.clientY
    const originX = geometry.x
    const originY = geometry.y
    const onMove = (move: PointerEvent): void => {
      geometry.x = clampGeometry(
        { ...geometry, x: originX + (move.clientX - startX) },
        win.innerWidth,
        win.innerHeight
      ).x
      geometry.y = clampGeometry(
        { ...geometry, y: originY + (move.clientY - startY) },
        win.innerWidth,
        win.innerHeight
      ).y
      applyGeometry(panel, geometry)
    }
    const onUp = (): void => {
      win.removeEventListener('pointermove', onMove)
      win.removeEventListener('pointerup', onUp)
      win.removeEventListener('pointercancel', onUp) // lost-pointer safety (cubic P2)
      saveGeometry(geometry, store)
    }
    win.addEventListener('pointermove', onMove)
    win.addEventListener('pointerup', onUp)
    win.addEventListener('pointercancel', onUp)
  }
  handle.addEventListener('pointerdown', onPointerDown)
  return () => handle.removeEventListener('pointerdown', onPointerDown)
}

function installResize(
  handle: HTMLElement,
  panel: HTMLElement,
  geometry: Geometry,
  store: Storage,
  win: Window
): () => void {
  const onPointerDown = (e: PointerEvent): void => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startY = e.clientY
    const originW = geometry.w
    const originH = geometry.h
    const onMove = (move: PointerEvent): void => {
      const next = clampGeometry(
        { ...geometry, w: originW + (move.clientX - startX), h: originH + (move.clientY - startY) },
        win.innerWidth,
        win.innerHeight
      )
      geometry.w = next.w
      geometry.h = next.h
      applyGeometry(panel, geometry)
    }
    const onUp = (): void => {
      win.removeEventListener('pointermove', onMove)
      win.removeEventListener('pointerup', onUp)
      win.removeEventListener('pointercancel', onUp) // lost-pointer safety (cubic P2)
      saveGeometry(geometry, store)
    }
    win.addEventListener('pointermove', onMove)
    win.addEventListener('pointerup', onUp)
    win.addEventListener('pointercancel', onUp)
  }
  handle.addEventListener('pointerdown', onPointerDown)
  return () => handle.removeEventListener('pointerdown', onPointerDown)
}
