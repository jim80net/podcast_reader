import { el } from '../dom'
import { emptyLibraryState } from '../empty-state'
import { extractEngineDetail } from '../engine-error'
import { formatDate, sourceLabel } from '../job-view'
import { LatestGate } from '../latest-gate'
import { hrefFor } from '../router'
import type { ViewCleanup } from '../store'
import type { LibraryEntry } from '../../../shared/types'

/**
 * Library view (app-views spec): cards (title, source, date) from
 * `GET /v1/library`, refreshed on `job_done` events and on hydration so a
 * completing job appears without a restart; empty state is a branded
 * first-transcript call-to-action toward New.
 */
export function mountLibrary(container: HTMLElement): ViewCleanup {
  const status = el('p', { class: 'view-status', text: 'Loading library…' })
  const list = el('div', { class: 'cards', attrs: { role: 'list' } })
  container.append(el('h2', { text: 'Library' }), status, list)

  let disposed = false
  // load() fires from several triggers (mount, job_done, hydration, engine
  // ready); the gate makes sure an out-of-order earlier response can never
  // overwrite the latest one.
  const gate = new LatestGate()

  async function load(): Promise<void> {
    const isLatest = gate.next()
    try {
      const entries = await window.api.listLibrary()
      if (disposed || !isLatest()) return
      render(entries)
    } catch (err) {
      if (disposed || !isLatest()) return
      status.textContent = `Library unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    }
  }

  function render(entries: LibraryEntry[]): void {
    status.textContent = ''
    status.classList.remove('error-text')
    list.replaceChildren()
    if (entries.length === 0) {
      const content = emptyLibraryState()
      list.append(
        el(
          'div',
          { class: 'empty-state' },
          el('div', { class: 'empty-mark', text: content.mark, attrs: { 'aria-hidden': 'true' } }),
          el('p', { class: 'empty-title', text: content.title }),
          el('p', { class: 'empty-lead', text: content.lead }),
          el('a', {
            class: 'button-link button-cta',
            text: content.cta.label,
            attrs: { href: content.cta.href }
          })
        )
      )
      return
    }
    const sorted = [...entries].sort((a, b) => b.created_at - a.created_at)
    for (const entry of sorted) {
      list.append(
        el(
          'a',
          {
            class: 'card',
            attrs: { href: hrefFor({ view: 'reader', sourceId: entry.source_id }), role: 'listitem' }
          },
          el('h3', { class: 'card-title', text: entry.title }),
          el('p', { class: 'card-source', text: sourceLabel(entry.source) }),
          el('time', { class: 'card-date', text: formatDate(entry.created_at) })
        )
      )
    }
  }

  void load()
  const unsubscribers = [
    // A finished job means a new artifact: refresh. Hydration after an SSE
    // (re)connect may have absorbed a missed job_done, so refresh then too.
    window.api.onPipelineEvent((event) => {
      if (event.kind === 'job_done') void load()
    }),
    window.api.onJobsHydrated(() => void load()),
    // The engine may not have been ready on first load; retry once it is.
    window.api.onEngineStatus((engineStatus) => {
      if (engineStatus.state === 'ready') void load()
    })
  ]

  return () => {
    disposed = true
    for (const unsubscribe of unsubscribers) unsubscribe()
  }
}
