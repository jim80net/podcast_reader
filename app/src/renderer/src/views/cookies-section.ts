import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { LatestGate } from '../latest-gate'
import { formatCaptureDate } from '../pairing-ui'
import type { CookieJarInfo } from '../../../shared/types'

/**
 * Settings → "Cookies" section (app-views spec, design decision 11): list
 * captured cookie-jar domains from `GET /v1/cookies` — metadata only, cookie
 * values have no IPC path — with per-domain delete, plus the honest
 * retention note (jars persist until deleted or replaced, per decision 9).
 */

export interface CookiesSection {
  cleanup(): void
}

export function mountCookiesSection(container: HTMLElement): CookiesSection {
  const status = el('p', { class: 'view-status', text: 'Loading captured logins…' })
  const list = el('div', { class: 'cookie-list', attrs: { role: 'list' } })
  container.append(
    el('h3', { text: 'Cookies' }),
    el('p', {
      class: 'section-note',
      text:
        'Logins shared from the browser extension are stored as cookie files ' +
        'the downloader uses for members-only sources. They stay until you ' +
        'delete or replace them here.'
    }),
    status,
    list
  )

  let disposed = false
  const gate = new LatestGate()

  async function load(): Promise<void> {
    const isLatest = gate.next()
    let jars: CookieJarInfo[]
    try {
      jars = await window.api.listCookieJars()
    } catch (err) {
      if (disposed || !isLatest()) return
      status.textContent = `Captured logins unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
      return
    }
    if (disposed || !isLatest()) return
    render(jars)
  }

  function render(jars: readonly CookieJarInfo[]): void {
    status.classList.remove('error-text')
    status.textContent = jars.length === 0 ? 'No captured logins.' : ''
    list.replaceChildren()
    for (const jar of jars) list.append(jarRow(jar))
  }

  function jarRow(jar: CookieJarInfo): HTMLElement {
    const deleteButton = el('button', {
      text: 'Delete',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    const rowError = el('p', { class: 'error-text', attrs: { role: 'alert' } })
    rowError.hidden = true
    deleteButton.addEventListener('click', () => {
      deleteButton.disabled = true
      window.api
        .deleteCookieJar(jar.domain)
        .then(() => {
          if (disposed) return
          // Re-enable before the refresh: a transient refresh failure must
          // not leave the row permanently disabled (cubic finding).
          deleteButton.disabled = false
          void load()
        })
        .catch((err: unknown) => {
          if (disposed) return
          rowError.textContent = `Delete failed: ${extractEngineDetail(err)}`
          rowError.hidden = false
          deleteButton.disabled = false
        })
    })
    return el(
      'div',
      { class: 'cookie-row', attrs: { role: 'listitem', 'data-domain': jar.domain } },
      el('span', { class: 'cookie-domain', text: jar.domain }),
      el('span', { class: 'cookie-date', text: `captured ${formatCaptureDate(jar.created_at)}` }),
      deleteButton,
      rowError
    )
  }

  void load()

  return {
    cleanup: () => {
      disposed = true
    }
  }
}
