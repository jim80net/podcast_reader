import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { mediaTerminalState } from '../media-events'
import { createMediaPlayer } from '../media-player'
import { hrefFor } from '../router'
import { createSyncBridge } from '../sync-bridge'
import type { MediaPlayer } from '../media-player'
import type { ViewCleanup } from '../store'
import type { SyncBridge } from '../sync-bridge'
import type { MediaInfo } from '../../../shared/types'

/**
 * Reader view (app-views spec + design decision 8): the artifact HTML is
 * fetched main-side with auth and injected verbatim via `iframe.srcdoc` with
 * `sandbox="allow-scripts"` ONLY — no `allow-same-origin`, so the artifact
 * (and its inline chapter scroll-sync script) runs in an opaque origin with
 * no parent access, no IPC bridge, and no engine token.
 *
 * Floating-video-player (media-playback spec, task 7.3): the Reader also
 * fetches `mediaInfo`, mounts the floating player BESIDE the transcript iframe
 * (artifact isolation preserved — coupling is purely postMessage), and wires
 * the bidirectional sync bridge. A `preparing` remote source shows a preparing
 * indication and resolves to playback on the media-prep `ready` event (with a
 * `mediaInfo` re-fetch fallback). Player + bridge + the SSE subscription are
 * torn down in ViewCleanup.
 */
export function mountReader(container: HTMLElement, sourceId: string): ViewCleanup {
  const status = el('p', { class: 'view-status', text: 'Loading transcript…' })
  const frame = el('iframe', {
    class: 'reader-frame',
    attrs: { sandbox: 'allow-scripts', title: 'Transcript' }
  })
  frame.hidden = true
  const mediaSlot = el('div', { class: 'media-slot' })
  const readerBody = el('div', { class: 'reader-body' }, mediaSlot, frame)
  // "Show video" restores a hidden media column (lives outside the column so it
  // survives the collapse); revealed only once a player is actually mounted.
  const showMediaBtn = el('button', {
    class: 'media-show',
    text: '▸ Show video',
    attrs: { type: 'button' }
  })
  showMediaBtn.hidden = true
  showMediaBtn.addEventListener('click', () => {
    readerBody.classList.remove('media-hidden')
    showMediaBtn.hidden = true
  })
  const hideMedia = (): void => {
    readerBody.classList.add('media-hidden')
    showMediaBtn.hidden = false
  }
  container.append(
    el(
      'p',
      { class: 'reader-back' },
      el('a', { text: '← Library', attrs: { href: hrefFor({ view: 'library' }) } }),
      showMediaBtn
    ),
    status,
    // Side-by-side: the player docks in a left column and the transcript fills
    // the rest at full height (stacks on narrow windows). An empty media slot
    // collapses, so a transcript-only Reader uses the full width.
    readerBody
  )

  let disposed = false
  let player: MediaPlayer | null = null
  let bridge: SyncBridge | null = null
  let unsubscribe: (() => void) | null = null
  // The transcript frame's post-load contentWindow is the only honored sync
  // source; defer player mounting until it (and the artifact's sync script)
  // exist, so the bridge binds a stable window.
  let frameLoaded = false
  let pendingInfo: MediaInfo | null = null

  const teardownPlayer = (): void => {
    bridge?.destroy()
    bridge = null
    player?.destroy()
    player = null
    unsubscribe?.()
    unsubscribe = null
  }

  const mountPlayer = (info: MediaInfo): void => {
    if (disposed || player !== null) return
    if (!frameLoaded) {
      pendingInfo = info // mount once the transcript frame is ready
      return
    }
    mediaSlot.replaceChildren()
    player = createMediaPlayer(sourceId, info, { onHide: hideMedia })
    mediaSlot.append(player.el)
    const frameWindow = frame.contentWindow
    if (frameWindow !== null) {
      bridge = createSyncBridge({ player, frameWindow })
    }
  }

  // F4 wait-contract: show a preparing indication, then resolve to playback on
  // the terminal media-prep event. Two races are closed (cubic P1): (1) a
  // `ready`/`unavailable` that fired between the first mediaInfo and this
  // subscription is recovered by an immediate post-subscribe recheck; (2) a
  // terminal `unavailable` clears the indicator instead of waiting forever.
  const waitForReady = (): void => {
    const preparing = el('p', { class: 'media-preparing', text: 'Preparing video…' })
    mediaSlot.append(preparing)

    const settle = (status: string): void => {
      if (disposed) return
      if (status === 'ready') {
        void window.api
          .mediaInfo(sourceId)
          .then((fresh) => {
            if (!disposed && fresh.status === 'ready') mountPlayer(fresh)
          })
          .catch(() => {
            /* transient: a later event or the next open retries */
          })
      } else if (status === 'unavailable') {
        preparing.remove() // give up gracefully: transcript-only
        unsubscribe?.()
        unsubscribe = null
      }
    }

    unsubscribe = window.api.onPipelineEvent((event) => {
      const state = mediaTerminalState(event, sourceId)
      if (state !== null) settle(state)
    })
    // Immediate recheck: catch a terminal transition that beat the subscription.
    void window.api
      .mediaInfo(sourceId)
      .then((fresh) => settle(fresh.status))
      .catch(() => {
        /* transient: the event path still applies */
      })
  }

  void window.api
    .mediaInfo(sourceId)
    .then((info) => {
      if (disposed) return
      // Unavailable → transcript-only, no player, no error spinner.
      if (info.kind === 'unavailable') return
      if (info.status === 'ready') mountPlayer(info)
      else if (info.status === 'preparing') waitForReady()
    })
    .catch(() => {
      // Media info unavailable (older engine, transient): degrade to
      // transcript-only rather than blocking the read.
    })

  frame.addEventListener('load', () => {
    if (disposed) return
    // about:blank fires load before srcdoc is set; only treat the artifact load
    // as ready (srcdoc has been assigned by then).
    if (frame.srcdoc === '') return
    frameLoaded = true
    if (pendingInfo !== null) {
      const info = pendingInfo
      pendingInfo = null
      mountPlayer(info)
    }
  })

  void window.api
    .transcriptHtml(sourceId)
    .then((html) => {
      if (disposed) return
      frame.srcdoc = html
      frame.hidden = false
      status.remove()
    })
    .catch((err: unknown) => {
      if (disposed) return
      status.textContent = `Transcript unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    })

  return () => {
    disposed = true
    teardownPlayer()
  }
}
