import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { mediaTerminalState } from '../media-events'
import { createMediaPlayer } from '../media-player'
import { createSyncBridge } from '../sync-bridge'
import { mountPremiumAdSlot } from '../premium-ad-slot'
import type { MediaPlayer } from '../media-player'
import type { ViewCleanup } from '../store'
import type { SyncBridge } from '../sync-bridge'
import type { MediaInfo } from '../../../shared/types'
import type { PremiumProductState } from '../../../shared/ipc'

/** Persisted "hide the media column" preference (the user may never use video). */
const MEDIA_HIDDEN_KEY = 'pr.media.hidden'

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
  const adSlot = el('div', { class: 'house-ad-slot', attrs: { 'data-slot': 'reader' } })
  // A permanent media toggle (shown only once a player exists): always visible
  // so the user can hide/show the current audio or video at will. The choice
  // persists across episodes and launches.
  const mediaToggle = el('button', { class: 'media-toggle', attrs: { type: 'button' } })
  mediaToggle.hidden = true
  const emailButton = el('button', {
    class: 'button-secondary',
    text: 'Email this transcript',
    attrs: { type: 'button' }
  })
  const emailStatus = el('p', {
    class: 'section-note',
    attrs: { role: 'status', 'aria-live': 'polite' }
  })
  const confirmEmail = el('button', {
    text: 'Send to Captured DEV mailbox',
    attrs: { type: 'button' }
  })
  const cancelEmail = el('button', {
    class: 'button-secondary',
    text: 'Cancel',
    attrs: { type: 'button' }
  })
  const confirmation = el(
    'section',
    { class: 'card email-confirmation', attrs: { 'aria-label': 'Confirm transcript email' } },
    el('h2', { text: 'Email this transcript?' }),
    el('p', {
      text: 'This transcript’s bounded plain text will be uploaded to the premium relay for this delivery only. The relay does not retain a copy after the delivery attempt. The Captured DEV mailbox retains the resulting email.'
    }),
    el('div', { class: 'button-row' }, confirmEmail, cancelEmail)
  )
  confirmation.hidden = true
  let premiumState: PremiumProductState = { state: 'local', available: true }
  let mediaKind: MediaInfo['kind'] = 'video'
  const setMediaHidden = (hidden: boolean, persist: boolean): void => {
    readerBody.classList.toggle('media-hidden', hidden)
    mediaToggle.textContent = mediaToggleText(mediaKind, hidden)
    if (persist) localStorage.setItem(MEDIA_HIDDEN_KEY, hidden ? '1' : '0')
  }
  mediaToggle.addEventListener('click', () =>
    setMediaHidden(!readerBody.classList.contains('media-hidden'), true)
  )
  const hideMedia = (): void => setMediaHidden(true, true)
  container.append(
    el('div', { class: 'reader-toolbar' }, mediaToggle, emailButton),
    emailStatus,
    confirmation,
    status,
    adSlot,
    // Side-by-side: the player docks in a left column and the transcript fills
    // the rest at full height (stacks on narrow windows). An empty media slot
    // collapses, so a transcript-only Reader uses the full width.
    readerBody
  )
  const cleanupAd = mountPremiumAdSlot(adSlot, 'reader')

  let disposed = false
  let player: MediaPlayer | null = null
  let bridge: SyncBridge | null = null
  let unsubscribe: (() => void) | null = null
  // The transcript frame's post-load contentWindow is the only honored sync
  // source; defer player mounting until it (and the artifact's sync script)
  // exist, so the bridge binds a stable window.
  let frameLoaded = false
  let pendingInfo: MediaInfo | null = null

  const renderEmail = (): void => {
    emailButton.disabled = !manualEmailAvailable(premiumState)
    emailStatus.textContent = manualEmailStatus(premiumState)
    if (!manualEmailAvailable(premiumState)) confirmation.hidden = true
  }
  emailButton.addEventListener('click', () => {
    if (!manualEmailAvailable(premiumState)) return
    confirmation.hidden = false
    emailStatus.textContent = 'Confirm this one transcript upload before sending.'
  })
  cancelEmail.addEventListener('click', () => {
    confirmation.hidden = true
    renderEmail()
  })
  confirmEmail.addEventListener('click', () => {
    if (!manualEmailAvailable(premiumState)) return
    confirmEmail.disabled = true
    cancelEmail.disabled = true
    void window.api.createManualEmail(sourceId).then((delivery) => {
      if (disposed) return
      confirmation.hidden = true
      emailStatus.textContent = delivery.state === 'delivered'
        ? 'Delivered to the Captured DEV mailbox.'
        : 'Queued for the Captured DEV mailbox.'
    }).catch(() => {
      if (!disposed) emailStatus.textContent = 'This transcript could not be queued for email.'
    }).finally(() => {
      if (!disposed) {
        confirmEmail.disabled = false
        cancelEmail.disabled = false
      }
    })
  })
  const unsubscribePremium = window.api.onPremiumState((next) => {
    premiumState = next
    if (!disposed) renderEmail()
  })
  void window.api.getPremiumState().then((next) => {
    if (!disposed) {
      premiumState = next
      renderEmail()
    }
  }).catch(() => {
    if (!disposed) {
      premiumState = { state: 'online-unavailable', available: true }
      renderEmail()
    }
  })
  renderEmail()

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
    // Reveal the permanent toggle + honor the persisted hidden preference now
    // that a player exists.
    mediaKind = info.kind
    mediaToggle.hidden = false
    setMediaHidden(localStorage.getItem(MEDIA_HIDDEN_KEY) === '1', false)
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
      // Inject the app's resolved theme so the transcript opens matching the
      // app (the artifact defaults to dark; the Reader follows the toggle).
      frame.srcdoc = withTheme(html, document.documentElement.dataset['theme'])
      frame.hidden = false
      status.remove()
    })
    .catch((err: unknown) => {
      if (disposed) return
      status.textContent = `Transcript unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    })

  // Live theme switching while the Reader is open: relay the app theme to the
  // sandboxed artifact via postMessage (its sync script sets data-theme).
  const onThemeChange = (event: Event): void => {
    const theme = (event as CustomEvent<string>).detail
    frame.contentWindow?.postMessage({ ch: 'pr-theme', theme }, '*')
  }
  window.addEventListener('pr-theme-change', onThemeChange)

  return () => {
    disposed = true
    window.removeEventListener('pr-theme-change', onThemeChange)
    teardownPlayer()
    cleanupAd()
    unsubscribePremium()
  }
}

export function mediaToggleText(kind: MediaInfo['kind'], hidden: boolean): string {
  const label = kind === 'audio' ? 'audio' : 'video'
  return hidden ? `▸ Show ${label}` : `▾ Hide ${label}`
}

export function manualEmailAvailable(state: PremiumProductState): boolean {
  return state.state === 'online-premium' && state.emailAvailable
}

export function manualEmailStatus(state: PremiumProductState): string {
  if (!state.available || state.state === 'local') return 'Connect an account to email this transcript.'
  if (state.state === 'online-free') return 'Transcript email requires premium.'
  if (state.state === 'online-unavailable') return 'Transcript email is paused while account status is unavailable.'
  if (!state.emailAvailable) return 'Transcript email is not enabled for this premium account.'
  return 'Email is delivered only to the Captured DEV mailbox.'
}

/** Set `data-theme` on the artifact's <html> so it opens in the app's theme. */
function withTheme(html: string, theme: string | undefined): string {
  if (theme !== 'light' && theme !== 'dark') return html
  // Insert data-theme as the first attribute of the opening <html> tag, however
  // it's written (`<html>` or `<html lang="en">`); replaces only the first match.
  return html.replace(/<html\b/i, `<html data-theme="${theme}"`)
}
