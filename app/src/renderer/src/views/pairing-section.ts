import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { combinedPairingString, pairingCountdown } from '../pairing-ui'

/**
 * Settings → "Connect browser extension" section (app-views spec, design
 * decision 11): a mint button → `pair:start` IPC → the combined
 * `<port>-<code>` paste string as the primary affordance, separate port/code
 * fields as fallback (per review adjudication), and a live expiry countdown.
 * Re-minting replaces the code (the engine invalidates the prior one). The
 * code is render-bound only — never stored or logged app-side.
 */

export interface PairingSection {
  cleanup(): void
}

export function mountPairingSection(container: HTMLElement): PairingSection {
  const mintButton = el('button', {
    text: 'Connect browser extension',
    attrs: { type: 'button', id: 'settings-pair-start' }
  })
  const display = el('div', { class: 'pairing-display' })
  display.hidden = true
  const combined = el('code', { class: 'pairing-combined', attrs: { id: 'settings-pair-combined' } })
  const portField = el('span', { class: 'pairing-field', attrs: { id: 'settings-pair-port' } })
  const codeField = el('span', { class: 'pairing-field', attrs: { id: 'settings-pair-code' } })
  const countdown = el('p', {
    class: 'pairing-countdown',
    attrs: { id: 'settings-pair-countdown', role: 'status' }
  })
  const error = el('p', { class: 'error-text', attrs: { role: 'alert' } })
  error.hidden = true
  display.append(
    el('p', { text: 'Paste this into the extension popup:' }),
    combined,
    el(
      'p',
      { class: 'pairing-fallback' },
      el('span', { text: 'Or enter separately — ' }),
      portField,
      el('span', { text: ' ' }),
      codeField
    ),
    countdown
  )
  container.append(
    el('h3', { text: 'Browser extension' }),
    el('p', {
      class: 'section-note',
      text:
        'Pair the Chrome extension to transcribe pages and share logins. ' +
        'The code below is single-use and expires after five minutes.'
    }),
    mintButton,
    display,
    error
  )

  let disposed = false
  let expiresAt: number | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  function stopTimer(): void {
    if (timer !== null) clearInterval(timer)
    timer = null
  }

  function tick(): void {
    if (expiresAt === null) return
    const { expired, label } = pairingCountdown(expiresAt, Date.now())
    countdown.textContent = label
    if (expired) {
      stopTimer()
      combined.classList.add('pairing-expired')
    }
  }

  mintButton.addEventListener('click', () => {
    mintButton.disabled = true
    error.hidden = true
    window.api
      .startPairing()
      .then((pairing) => {
        if (disposed) return
        mintButton.textContent = 'Mint a new code'
        combined.textContent = combinedPairingString(pairing.port, pairing.code)
        combined.classList.remove('pairing-expired')
        portField.textContent = `Port: ${pairing.port}`
        codeField.textContent = `Code: ${pairing.code}`
        display.hidden = false
        expiresAt = pairing.expires_at
        stopTimer()
        timer = setInterval(tick, 1000)
        tick()
      })
      .catch((err: unknown) => {
        if (disposed) return
        error.textContent = `Pairing unavailable: ${extractEngineDetail(err)}`
        error.hidden = false
      })
      .finally(() => {
        if (!disposed) mintButton.disabled = false
      })
  })

  return {
    cleanup: () => {
      disposed = true
      stopTimer()
    }
  }
}
